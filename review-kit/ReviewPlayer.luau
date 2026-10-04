--!strict
-- ReviewPlayer — lecteur de relecture pour un film Rive CLI : boucle, lecture / pause, tête de lecture qu'on
-- clique ou qu'on glisse, compteur, bande-son recalée sur la position.
--
-- Contrat (voir README.md) : tout le film est une fonction d'UN nombre, le `time` (fraction 0..1) de la
-- NestedRemapAnimation du film imbriqué dans l'artboard de relecture, lié à la propriété de ViewModel `progress`.
-- Ce script écrit `progress` à chaque frame, plus ce que la barre affiche : `headX` (x de la tête), `timeText`,
-- `playOp` / `pauseOp` (opacité des icônes). Il reçoit tous les événements pointeur de l'artboard :
--   barre (entre barY0 et barY1)          -> clic = aller là, glisser = scrubber (la lecture reprend au relâcher)
--   bouton (cercle btnX, btnY, btnR)      -> lecture / pause
--   image du film (au-dessus de filmBottom) -> lecture / pause
-- Le son : AudioAsset nommé `audio` (vide = muet). Audio.play rend nil en headless (--screenshot) : tout reste
-- silencieux et le reste fonctionne.

type Review = {
    duration: Input<number>,
    barX0: Input<number>,
    barX1: Input<number>,
    barY0: Input<number>,
    barY1: Input<number>,
    btnX: Input<number>,
    btnY: Input<number>,
    btnR: Input<number>,
    filmBottom: Input<number>,
    startPaused: Input<boolean>,
    audio: Input<string>,

    context: Context,
    bound: boolean,
    progress: Property<number>?,
    headX: Property<number>?,
    timeText: Property<string>?,
    playOp: Property<number>?,
    annOn: Property<number>?,
    seekTo: Property<number>?,
    cmd: Property<number>?,
    holdPlay: Property<number>?,
    pauseOp: Property<number>?,
    t: number,
    playing: boolean,
    dragging: boolean,
    wasPlaying: boolean,
    started: boolean,
    src: AudioSource?,
    sound: AudioSound?,
}

local function clamp(v: number, a: number, b: number): number
    if v < a then
        return a
    elseif v > b then
        return b
    end
    return v
end

local function bindVM(self: Review)
    if self.bound then
        return
    end
    local vm = self.context:viewModel()
    if vm == nil then
        return
    end
    self.progress = vm:getNumber("progress")
    self.headX = vm:getNumber("headX")
    self.timeText = vm:getString("timeText")
    self.playOp = vm:getNumber("playOp")
    self.pauseOp = vm:getNumber("pauseOp")
    self.annOn = vm:getNumber("annOn")        -- 1 = le crayon dessine : le lecteur lâche le pointeur
    self.holdPlay = vm:getNumber("holdPlay")  -- 1 = on écrit une note : le film reste figé
    self.seekTo = vm:getNumber("seekTo")      -- le temps d'une note : on y va, puis -1
    self.cmd = vm:getNumber("cmd")            -- 1 = lecture/pause, 2 = image -1, 3 = image +1
    self.bound = true
end

local function startSound(self: Review)
    if self.audio == "" then
        return
    end
    if self.src == nil then
        self.src = self.context:audio(self.audio)
    end
    local src = self.src
    if src then
        local s = Audio.play(src)
        self.sound = s
        if s then
            s:seek(self.t)
        end
    end
end

local function stopSound(self: Review)
    local s = self.sound
    if s then
        s:stop()
    end
    self.sound = nil
end

local function play(self: Review)
    self.playing = true
    stopSound(self)
    startSound(self)
end

local function pause(self: Review)
    self.playing = false
    local s = self.sound
    if s then
        s:pause()
    end
end

local function toggle(self: Review)
    if self.playing then
        pause(self)
    else
        play(self)
    end
end

local function xToTime(self: Review, x: number): number
    local w = self.barX1 - self.barX0
    if w <= 0 then
        return 0
    end
    return clamp((x - self.barX0) / w, 0, 0.9999) * self.duration
end

local function write(self: Review)
    local d = self.duration
    local p = if d > 0 then self.t / d else 0
    local pr = self.progress
    if pr then
        pr.value = p
    end
    local hx = self.headX
    if hx then
        hx.value = self.barX0 + (self.barX1 - self.barX0) * p
    end
    local tt = self.timeText
    if tt then
        tt.value = string.format("%.1f s", self.t)
    end
    local po, pa = self.playOp, self.pauseOp
    if po then
        po.value = if self.playing then 0 else 1
    end
    if pa then
        pa.value = if self.playing then 1 else 0
    end
end

function init(self: Review, context: Context): boolean
    self.context = context
    self.playing = not self.startPaused
    return true
end

function advance(self: Review, seconds: number): boolean
    bindVM(self)
    local cm = self.cmd
    if cm and cm.value > 0 then
        local c = cm.value
        cm.value = 0
        if c == 1 then
            toggle(self)
        else
            local step = 1 / 60
            if self.playing then
                toggle(self)
            end
            self.t = clamp(self.t + (if c == 2 then -step else step), 0, self.duration)
        end
    end
    local sk = self.seekTo
    if sk and sk.value >= 0 then
        self.t = clamp(sk.value, 0, self.duration)
        sk.value = -1
        -- aller sur une note, c'est se poser sur SON image : on s'y arrête (le dessin de la note
        -- n'est visible qu'à cet instant, il filerait en une demi-seconde si la lecture continuait)
        stopSound(self)
        self.playing = false
        self.dragging = false
        self.wasPlaying = false
    end
    -- en mode annotation le film est figé : la note appartient à l'image qu'on regarde
    local hp = self.holdPlay
    local a = self.annOn
    if (a ~= nil and a.value >= 0.5) or (hp ~= nil and hp.value >= 0.5) then
        if self.playing or self.sound ~= nil then
            stopSound(self)          -- pas un pause : la piste est relâchée, elle ne peut plus filer toute seule
            self.playing = false
        end
        self.dragging = false
        self.wasPlaying = false
        write(self)
        return true
    end
    if not self.started then
        self.started = true
        if self.playing then
            startSound(self)
        end
    end
    if self.playing and not self.dragging then
        self.t += seconds
        if self.t >= self.duration then
            self.t -= self.duration
            stopSound(self)
            startSound(self)
        end
    end
    write(self)
    return true
end

-- le mode annotation (ReviewNotes) prend la main sur le pointeur : le lecteur ne scrubbe ni ne bascule.
-- ÉCRIRE compte autant que DESSINER : sans `holdPlay` ici, le clic qui sort du champ (sur l'image,
-- sur la barre) arrivait aussi au lecteur, qui relançait le film — la note posée semblait s'effacer.
local function annotating(self: Review): boolean
    local a, h = self.annOn, self.holdPlay
    return (a ~= nil and a.value >= 0.5) or (h ~= nil and h.value >= 0.5)
end

function pointerDown(self: Review, event: PointerEvent)
    if annotating(self) then
        return
    end
    local p = event.position
    if p.y >= self.barY0 and p.y <= self.barY1 then
        self.dragging = true
        self.wasPlaying = self.playing
        local s = self.sound
        if s and self.playing then
            s:pause()
        end
        self.t = xToTime(self, p.x)
        event:hit()
        return
    end
    local dx, dy = p.x - self.btnX, p.y - self.btnY
    if dx * dx + dy * dy <= self.btnR * self.btnR or p.y < self.filmBottom then
        toggle(self)
        event:hit()
    end
end

function pointerMove(self: Review, event: PointerEvent)
    if annotating(self) then
        return
    end
    if self.dragging then
        self.t = xToTime(self, event.position.x)
        event:hit()
    end
end

local function release(self: Review)
    if not self.dragging then
        return
    end
    self.dragging = false
    if self.wasPlaying then
        play(self)
    end
end

function pointerUp(self: Review, event: PointerEvent)
    if annotating(self) then
        self.dragging = false
        self.wasPlaying = false      -- on est en train d'annoter : rien ne redémarre au relâcher
        return
    end
    if self.dragging then
        release(self)
        event:hit()
    end
end

function pointerExit(self: Review, event: PointerEvent)
    if annotating(self) then
        self.dragging = false
        self.wasPlaying = false
        return
    end
    release(self)
end

return function(): Node<Review>
    return {
        duration = 10,
        barX0 = 0,
        barX1 = 100,
        barY0 = 0,
        barY1 = 10,
        btnX = 0,
        btnY = 0,
        btnR = 0,
        filmBottom = 0,
        startPaused = false,
        audio = "",

        context = late(),
        bound = false,
        progress = nil,
        annOn = nil,
        seekTo = nil,
        cmd = nil,
        holdPlay = nil,
        headX = nil,
        timeText = nil,
        playOp = nil,
        pauseOp = nil,
        t = 0,
        playing = true,
        dragging = false,
        wasPlaying = false,
        started = false,
        src = nil,
        sound = nil,

        init = init,
        advance = advance,
        pointerDown = pointerDown,
        pointerMove = pointerMove,
        pointerUp = pointerUp,
        pointerExit = pointerExit,
    }
end
