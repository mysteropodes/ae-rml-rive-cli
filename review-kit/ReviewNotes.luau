--!strict
-- ReviewNotes — la couche d'annotation de la relecture : dessiner sur l'image, taper une note, la
-- poser à l'instant courant du film, et la rendre à l'agent. Voir README.md § « Annoter ».
--
-- Pourquoi un ScriptedLayout et pas un drawable : le clavier n'arrive qu'à un script qui a un
-- <FocusData/> en enfant direct, et le player du CLI ne focalise que celui d'un ScriptedLayout
-- (mesuré). `textEvent` livre le texte composé, accents compris ; `keyboardEvent` sert aux touches
-- de contrôle. La couche est le PREMIER enfant de l'artboard : elle dessine au-dessus et voit les
-- événements pointeur avant le lecteur.
--
-- Sortie : une ligne par note sur la sortie standard du viewer (le seul canal d'un script), relue
-- par review_notes.py :
--     #ANN {"id":3,"t":12.34,"text":"la main part trop tôt","color":4,"pts":[x,y,x,y,…]}
--     #ANNDEL 3       (la croix d'une ligne, ou une note vidée)
--     #ANNRES 3 1     (la coche : traitée / à faire)
-- Les coordonnées sont celles de l'artboard de relecture (origine en haut à gauche, image en haut).
--
-- Modes : idle -> (frappe, clic dans le champ, ou clic sur une couleur / la brosse) -> arm : le film se met en pause, on dessine au
-- doigt/souris sur l'image, on tape la note, Entrée valide, Échap annule, ⌫ efface une lettre (et,
-- sur un texte vide, le dernier trait).

-- pts = x1,y1,x2,y2,… aplati, -1 = fin de trait ; done = traitée ; c = la couleur du trait (1-4)
type Note = { id: number, t: number, text: string, pts: { number }, done: boolean, c: number }

type Notes = {
    duration: Input<number>,
    filmBottom: Input<number>,     -- l'image du film s'arrête là : au-dessus on dessine, en dessous c'est la barre
    barX0: Input<number>,
    barX1: Input<number>,
    barY: Input<number>,           -- l'axe de la barre : les repères des notes se posent dessus
    penX: Input<number>,           -- le bouton brosse, dans la barre d'outils du champ
    penY: Input<number>,
    penR: Input<number>,
    textX: Input<number>,          -- le texte du champ : bord gauche, ligne du bas, largeur utile
    textBase: Input<number>,
    fieldW: Input<number>,
    boxBgH: Property<number>?,     -- le fond du champ grandit avec le texte (le bas reste en place)
    boxBgY: Property<number>?,
    boxAtY: Property<number>?,     -- le timecode monte avec lui
    lines: number,                 -- le nombre de lignes que le texte occupe
    dotX: Input<number>,           -- les 4 pastilles de couleur : première, pas, rayon
    dotGap: Input<number>,
    dotR: Input<number>,
    colX: Input<number>,           -- la colonne « Notes » : bord gauche, largeur, première ligne, pas, nombre de lignes
    colW: Input<number>,
    rowY0: Input<number>,
    rowH: Input<number>,
    rows: Input<number>,
    fps: Input<number>,             -- pour l'image par image (flèches)
    saved: Input<string>,          -- les notes déjà prises, rechargées au démarrage (review_notes.json)

    context: Context,
    bound: boolean,
    progress: Property<number>?,   -- écrit par ReviewPlayer, lu ici pour dater la note
    annOn: Property<number>?,      -- 1 = mode annotation (ReviewPlayer lâche le pointeur et se met en pause)
    noteText: Property<string>?,   -- le texte en cours de frappe (affiché par un Text du panneau)
    inDown: boolean,               -- un appui est en cours : on ne rend pas le pointeur au lecteur
    unlock: boolean,               -- une libération attend le relâcher
    noteHint: Property<string>?,   -- la ligne d'aide / le compte
    phA: Property<number>?,        -- l'invite « Écrire un commentaire… » (1 = visible)
    noteAt: Property<string>?,     -- le timecode affiché dans le champ
    sendA: Property<number>?,      -- le bouton d'envoi : 1 = actif, 0,25 = éteint
    holdPlay: Property<number>?,   -- 1 = on écrit : le film reste figé (le pointeur, lui, reste au lecteur)
    pen: boolean,                  -- l'outil brosse : lui seul prend le pointeur sur l'image
    col: number,                   -- la couleur choisie (1-4)
    noteCount: Property<string>?,  -- « 3 notes » en tête de colonne
    boxY: Input<number>,           -- le champ de saisie : haut, hauteur (clic = commencer à écrire)
    boxH: Input<number>,
    sendX: Input<number>,          -- le bouton « Envoyer »
    sendY: Input<number>,
    sendW: Input<number>,
    sendH: Input<number>,
    cmd: Property<number>?,        -- 1 = lecture/pause, 2 = image précédente, 3 = image suivante (le lecteur exécute puis remet 0)
    seekTo: Property<number>?,     -- le lecteur y saute puis remet -1 (clic sur une ligne du fil)
    selY: Property<number>?,       -- le surlignage de la ligne choisie
    selA: Property<number>?,
    rowT: { Property<string>? },   -- le fil : texte, temps affiché, opacité par ligne
    rowX: { Property<string>? },
    rowA: { Property<number>? },
    sel: number,                   -- la note choisie dans le fil (id), 0 = aucune
    hover: number,                 -- la ligne survolée (index d'affichage), 0 = aucune
    editing: number,               -- la note en cours de correction (id), 0 = nouvelle
    top: number,                   -- première note affichée (défilement du fil)
    notes: { Note },
    dirty: boolean,                -- le fil doit être réécrit
    cur: { number },               -- les traits de la note en cours
    drawing: boolean,
    arm: boolean,
    text: string,
    nextId: number,
    t: number,
    path: Path,
    path2: Path,
    inks: { Paint },               -- le trait en cours, par couleur
    inksOld: { Paint },            -- le trait d'une note déjà posée, par couleur
    fills: { Paint },              -- la pastille pleine, par couleur
    okInk: Paint,                  -- la coche d'une note traitée
    dim: Paint,                    -- les actions au repos (coche grise, croix)
    ink: Paint,
    inkOld: Paint,
    dot: Paint,
    ring: Paint,
    edge: Paint,
    frame: Paint,
    ringOn: Paint,
    penRing: Paint,
    penDisc: Paint,
    penInk: Paint,        -- le crayon dessiné dans le bouton : rouge au repos, sombre sur le disque plein
}

local INK, INK_OLD, DOT = 0xFFFF4D6A, 0x66FF4D6A, 0xFFFF4D6A
-- la palette de la brosse : une seule brosse, quatre couleurs (la 4e est celle de l'interface)
local PALETTE = { 0xFF38BDF8, 0xFF34D399, 0xFFF59E0B, 0xFFFF4D6A }
local PALETTE_SOFT = { 0x9938BDF8, 0x9934D399, 0x99F59E0B, 0x99FF4D6A }
local function colorOf(n: number): number
    return if n >= 1 and n <= 4 then n else 4
end

local function bindVM(self: Notes)
    if self.bound then
        return
    end
    local vm = self.context:viewModel()
    if vm == nil then
        return
    end
    self.progress = vm:getNumber("progress")
    self.annOn = vm:getNumber("annOn")
    self.noteText = vm:getString("noteText")
    self.noteHint = vm:getString("noteHint")
    self.phA = vm:getNumber("phA")
    self.noteAt = vm:getString("noteAt")
    self.sendA = vm:getNumber("sendA")
    self.holdPlay = vm:getNumber("holdPlay")
    self.noteCount = vm:getString("noteCount")
    self.boxBgH = vm:getNumber("boxBgH")
    self.boxBgY = vm:getNumber("boxBgY")
    self.boxAtY = vm:getNumber("boxAtY")
    self.cmd = vm:getNumber("cmd")
    self.seekTo = vm:getNumber("seekTo")
    self.selY = vm:getNumber("selY")
    self.selA = vm:getNumber("selA")
    for r = 1, self.rows do
        self.rowT[r] = vm:getString(`rowT{r}`)
        self.rowX[r] = vm:getString(`rowX{r}`)
        self.rowA[r] = vm:getNumber(`rowA{r}`)
    end
    self.bound = true
    self.dirty = true
end

-- une seconde -> « 12.3 s »
local function stamp(t: number): string
    local m = math.floor(t / 60)
    local sec = t - m * 60
    return string.format("%d:%05.2f", m, sec)
end

local function short(s: string, n: number): string
    if #s <= n then
        return s
    end
    return string.sub(s, 1, n - 1) .. "…"
end

-- le fil de notes de la colonne : une ligne par note (les plus récentes d'abord dans le temps du
-- film), la note choisie surlignée. Réécrit seulement quand quelque chose a bougé.
local function writeRows(self: Notes)
    if not self.dirty then
        return
    end
    self.dirty = false
    local n = #self.notes
    local first = math.clamp(self.top, 1, math.max(1, n - self.rows + 1))
    self.top = first
    local selRow = 0
    for r = 1, self.rows do
        local k = first + r - 1
        local note: Note? = if k <= n then self.notes[k] else nil
        local pt, px, pa = self.rowT[r], self.rowX[r], self.rowA[r]
        if pt then
            local body = if note then (if note.text ~= "" then note.text else "(drawing)") else ""
            pt.value = if note then short(body, 30) else ""
        end
        if px then
            px.value = if note then stamp(note.t) else ""
        end
        if pa then
            pa.value = if note then (if note.done then 0.45 elseif r == self.hover then 1 else 0.85) else 0
        end
        if note and note.id == self.sel then
            selRow = r
        end
    end
    local sy, sa = self.selY, self.selA
    if sy and sa then
        sa.value = if selRow > 0 then 1 else 0
        sy.value = self.rowY0 + (selRow - 1) * self.rowH - 6
    end
end

-- la ligne sous la boîte : ce qu'on peut faire là, maintenant (comme le « Leave a comment » de Frame.io)
-- combien de lignes le texte occupe : la largeur d'un signe est estimée (un script n'a pas les
-- métriques de la police), mais l'erreur ne change que le moment où la boîte grandit
local LINE_H, CHAR_W = 22, 7.6

local function layout(self: Notes)
    local per = math.max(8, math.floor(self.fieldW / CHAR_W))
    local n = 1
    local run = 0
    for _, cp in utf8.codes(self.text) do
        run += 1
        if cp == 32 and run > per - 6 then      -- on coupe au mot quand la ligne est pleine
            n += 1
            run = 0
        elseif run > per then
            n += 1
            run = 0
        end
    end
    self.lines = math.min(n, 5)
    local grow = (self.lines - 1) * LINE_H
    local h, y, at = self.boxBgH, self.boxBgY, self.boxAtY
    if h then
        h.value = self.boxH + grow
    end
    if y then
        y.value = self.boxY + self.boxH / 2 - grow / 2
    end
    if at then
        at.value = -grow
    end
end

-- le curseur est un signe DANS le texte : il suit le retour à la ligne, et ne peut pas se décaler
local function paintText(self: Notes)
    local p = self.noteText
    if p == nil then
        return
    end
    if self.arm and math.floor(self.t * 2) % 2 == 0 then
        p.value = self.text .. "|"
    else
        p.value = self.text
    end
    layout(self)
end

local function hint(self: Notes)
    local p = self.noteHint
    if p == nil then
        return
    end
    local ph, at = self.phA, self.noteAt
    if ph then
        ph.value = if self.text == "" then 1 else 0        -- « Écrire un commentaire… »
    end
    if at then
        local prog2 = self.progress
        local now2 = (if prog2 then prog2.value else 0) * self.duration
        at.value = if self.editing > 0 then string.format("%s  ·  editing #%d", stamp(now2), self.editing) else stamp(now2)
    end
    local sa = self.sendA
    if sa then
        sa.value = if self.arm and (self.text ~= "" or #self.cur > 0) then 1 else 0.22
    end
    local n = #self.notes
    local nc = self.noteCount
    if nc then
        nc.value = if n == 0 then "" else string.format("%d", n)
    end
    -- court : l'aide tient à gauche du bouton « Envoyer »
    if self.arm and self.pen then
        p.value = "draw on the frame · Enter to post"
    elseif self.arm then
        p.value = "Enter to post · Esc to cancel"
    else
        p.value = "type, or click here"
    end
end

-- Rendre la main au lecteur au milieu d'un clic, c'est lui offrir ce clic-là : la couche est
-- appelée AVANT lui, il verrait un clic ordinaire sur l'image et relancerait le film (la note
-- posée aurait l'air de s'effacer). On ne relâche donc jamais pendant un appui : `unlock` retient
-- la baisse, `pointerUp` la applique.
local function locks(self: Notes, allowRelease: boolean)
    local a, h = self.annOn, self.holdPlay
    local wantA = if self.pen then 1 else 0
    local wantH = if self.arm then 1 else 0
    if a then
        if wantA == 1 or allowRelease then
            a.value = wantA
        elseif a.value >= 0.5 then
            self.unlock = true
        end
    end
    if h then
        if wantH == 1 or allowRelease then
            h.value = wantH
        elseif h.value >= 0.5 then
            self.unlock = true
        end
    end
end

local function setPen(self: Notes, on: boolean)
    self.pen = on
    self.drawing = false
    locks(self, not self.inDown)           -- seul le crayon prend le pointeur au lecteur
end

local function setArm(self: Notes, on: boolean)
    self.arm = on
    self.drawing = false
    if not on then
        self.cur = {}
        self.text = ""
        setPen(self, false)
    end
    locks(self, not self.inDown)            -- on écrit : le film ne repart pas tout seul
    paintText(self)
    hint(self)
end

-- la note en cours part sur la sortie standard, puis reste en mémoire pour être redessinée
local function commit(self: Notes)
    if not self.arm then
        return
    end
    local id = if self.editing > 0 then self.editing else self.nextId
    if #self.cur == 0 and self.text == "" then
        if self.editing > 0 then                -- vidée : on la supprime
            for k, n in self.notes do
                if n.id == id then
                    table.remove(self.notes, k)
                    break
                end
            end
            print(string.format("#ANNDEL %d", id))
            self.sel = 0
            self.dirty = true
        end
        setArm(self, false)
        return
    end
    local prog = self.progress
    local t = (if prog then prog.value else 0) * self.duration
    local pts = table.clone(self.cur)
    local slot = 0
    for k, n in self.notes do
        if n.id == id then
            slot = k
        end
    end
    local note: Note = { id = id, t = t, text = self.text, pts = pts, done = false, c = self.col }
    if slot > 0 then
        self.notes[slot] = note                -- correction d'une note existante
    else
        table.insert(self.notes, note)
        self.nextId += 1
    end
    table.sort(self.notes, function(a: Note, b: Note): boolean
        return a.t < b.t
    end)
    self.sel = id
    self.dirty = true
    local parts = table.create(#pts)
    for k, v in pts do
        parts[k] = string.format("%.1f", v)
    end
    print(string.format('#ANN {"id":%d,"t":%.3f,"text":%q,"color":%d,"pts":[%s]}',
                        id, t, self.text, self.col, table.concat(parts, ",")))
    self.editing = 0
    setArm(self, false)
end

-- « id|t|texte|x,y,x,y,… » par ligne -> la liste des notes (ce que le générateur a relu du JSON)
local function restore(self: Notes)
    local src = self.saved
    if src == "" then
        return
    end
    for line in string.gmatch(src, "[^\n]+") do
        local a = string.find(line, "|", 1, true)
        local b = if a then string.find(line, "|", a + 1, true) else nil
        local c = if b then string.find(line, "|", b + 1, true) else nil
        if a and b and c then
            local id = tonumber(string.sub(line, 1, a - 1)) or 0
            local t = tonumber(string.sub(line, a + 1, b - 1)) or 0
            local text = string.sub(line, b + 1, c - 1)
            local rest = string.sub(line, c + 1)
            local d = string.find(rest, "|", 1, true)
            local done, col = false, 4
            if d then
                local tail = string.sub(rest, d + 1)
                rest = string.sub(rest, 1, d - 1)
                local e = string.find(tail, "|", 1, true)
                if e then
                    col = colorOf(tonumber(string.sub(tail, e + 1)) or 4)
                    tail = string.sub(tail, 1, e - 1)
                end
                done = tail == "1"
            end
            local pts: { number } = {}
            for v in string.gmatch(rest, "[^,]+") do
                table.insert(pts, tonumber(v) or 0)
            end
            table.insert(self.notes, { id = id, t = t, text = text, pts = pts, done = done, c = col })
            if id >= self.nextId then
                self.nextId = id + 1
            end
        end
    end
    table.sort(self.notes, function(x: Note, y: Note): boolean
        return x.t < y.t
    end)
    self.dirty = true
end

local function init(self: Notes, context: Context): boolean
    self.context = context
    restore(self)
    return true
end

local function advance(self: Notes, dt: number): boolean
    bindVM(self)
    self.t += dt
    -- le fil suit la tête de lecture : la note de l'instant se surligne et défile dans la vue
    if not self.arm then
        local prog = self.progress
        local now = (if prog then prog.value else 0) * self.duration
        local best, bestD = 0, 0.3
        local at = 0
        for k, n in self.notes do
            local d = math.abs(n.t - now)
            -- la note déjà choisie garde la main tant qu'elle est dans la fenêtre : deux notes
            -- posées au même instant ne doivent pas se voler le surlignage
            if d < bestD or (d <= 0.3 and n.id == self.sel) then
                best, bestD, at = n.id, (if n.id == self.sel then -1 else d), k
            end
        end
        if best ~= 0 and best ~= self.sel then
            self.sel = best
            if at < self.top then
                self.top = at
            elseif at > self.top + self.rows - 1 then
                self.top = at - self.rows + 1
            end
            self.dirty = true
        end
    end
    hint(self)
    paintText(self)
    writeRows(self)
    return true
end

local function resize(self: Notes, size: Vector) end

local function inPen(self: Notes, x: number, y: number): boolean
    local dx, dy = x - self.penX, y - self.penY
    return dx * dx + dy * dy <= self.penR * self.penR
end

local function rowAt(self: Notes, x: number, y: number): number
    if x < self.colX then
        return 0
    end
    local r = math.floor((y - self.rowY0 + self.rowH / 2) / self.rowH) + 1
    if r < 1 or r > self.rows or self.top + r - 1 > #self.notes then
        return 0
    end
    return r
end

-- ouvrir une note pour la corriger : son texte et ses traits reviennent dans l'éditeur
local function edit(self: Notes, id: number)
    for _, n in self.notes do
        if n.id == id then
            self.editing = id
            self.text = n.text
            self.cur = table.clone(n.pts)
            setArm(self, true)
            local p = self.noteText
            if p then
                p.value = self.text
            end
            local sk = self.seekTo
            if sk then
                sk.value = n.t
            end
            return
        end
    end
end

-- quitter le champ : ce qui est écrit ou dessiné est posé, un brouillon vide est jeté
local function blur(self: Notes)
    if not self.arm then
        return
    end
    if self.text ~= "" or #self.cur > 0 then
        commit(self)
    else
        setArm(self, false)
    end
end

local function down(self: Notes, event: PointerEvent)
    bindVM(self)
    local x, y = event.position.x, event.position.y
    -- les quatre couleurs de la brosse
    if math.abs(y - self.penY) <= self.dotR + 6 then
        for k = 1, 4 do
            if math.abs(x - (self.dotX + (k - 1) * self.dotGap)) <= self.dotR + 6 then
                self.col = k
                if not self.arm then
                    self.editing = 0
                    setArm(self, true)
                end
                setPen(self, true)           -- choisir une couleur, c'est prendre la brosse
                self.dirty = true
                event:hit()
                return
            end
        end
    end
    -- un point de la bande des commentaires : y aller et choisir la note (marqueur Frame.io)
    if math.abs(y - self.barY) <= 14 then
        local span = math.max(self.barX1 - self.barX0, 1)
        for _, n in self.notes do
            local nx = self.barX0 + span * n.t / math.max(self.duration, 0.001)
            if math.abs(x - nx) <= 12 then
                blur(self)
                self.sel = n.id
                self.dirty = true
                local sk = self.seekTo
                if sk then
                    sk.value = n.t
                end
                event:hit()
                return
            end
        end
    end
    -- la brosse : un outil, pas un mode ; elle ouvre le commentaire s'il n'y en a pas
    if inPen(self, x, y) then
        if not self.arm then
            self.editing = 0
            setArm(self, true)
        end
        setPen(self, not self.pen)
        event:hit()
        return
    end
    -- le bouton « Envoyer »
    if x >= self.sendX and x <= self.sendX + self.sendW and y >= self.sendY and y <= self.sendY + self.sendH then
        if self.arm then
            commit(self)
        end
        event:hit()
        return
    end
    -- le champ de saisie : cliquer dedans ouvre le commentaire (comme Frame.io)
    if x >= self.colX and y >= self.boxY - (self.lines - 1) * LINE_H and y <= self.boxY + self.boxH then
        if not self.arm then
            self.editing = 0
            setArm(self, true)
        end
        event:hit()
        return
    end
    -- la colonne : le timecode = y aller, la ligne choisie = la rouvrir, la pastille = traitée
    if x >= self.colX then
        local r = rowAt(self, x, y)
        if r == 0 then
            blur(self)          -- le vide de la colonne, sous le fil : on sort de la saisie
            event:hit()
            return
        end
        local note = self.notes[self.top + r - 1]
        local id, t = note.id, note.t                  -- blur() peut retrier les notes : on garde l'id
        if note.id == self.sel and x >= self.colX + self.colW - 46 then
            for k, n in self.notes do                  -- la croix : supprimer la note
                if n.id == id then
                    table.remove(self.notes, k)
                    break
                end
            end
            print(string.format("#ANNDEL %d", id))
            self.sel = 0
            if self.editing == id then
                self.editing = 0
                setArm(self, false)
            end
            self.dirty = true
        elseif note.id == self.sel and x >= self.colX + self.colW - 82 then
            note.done = not note.done                  -- la coche : traitée / à faire
            print(string.format("#ANNRES %d %d", id, if note.done then 1 else 0))
            self.dirty = true
        else
            local was = self.arm
            if was then
                blur(self)                             -- le brouillon en cours est posé avant de changer de note
            end
            if self.sel == id and not was then
                edit(self, id)
            else
                self.sel = id
                self.dirty = true
                local sk = self.seekTo
                if sk then
                    sk.value = t
                end
            end
        end
        event:hit()
        return
    end
    if self.pen and self.arm and y < self.filmBottom then
        self.drawing = true
        table.insert(self.cur, x)
        table.insert(self.cur, y)
        event:hit()
        return
    end
    blur(self)      -- clic ailleurs (image sans crayon, barre, panneau) : on sort de la saisie
end

local function pointerMove(self: Notes, event: PointerEvent)
    local h = rowAt(self, event.position.x, event.position.y)
    if h ~= self.hover then
        self.hover = h
        self.dirty = true
    end
    if not (self.pen and self.drawing) then
        return
    end
    local x, y = event.position.x, event.position.y
    local n = #self.cur
    if n >= 2 and self.cur[n - 1] == x and self.cur[n] == y then
        return
    end
    table.insert(self.cur, x)
    table.insert(self.cur, y)
    event:hit()
end

local function pointerDown(self: Notes, event: PointerEvent)
    self.inDown = true
    down(self, event)
    self.inDown = false
end

-- le relâcher : c'est ici que le lecteur récupère le pointeur, jamais avant
local function letGo(self: Notes)
    if self.unlock then
        self.unlock = false
        locks(self, true)
    end
end

local function pointerUp(self: Notes, event: PointerEvent)
    if self.pen and self.drawing then
        self.drawing = false
        table.insert(self.cur, -1)          -- fin de trait
        table.insert(self.cur, -1)
        event:hit()
    end
    letGo(self)
end

local function pointerExit(self: Notes, event: PointerEvent)
    if self.drawing then
        self.drawing = false
        table.insert(self.cur, -1)
        table.insert(self.cur, -1)
    end
    letGo(self)
end

local function keyboardEvent(self: Notes, event: KeyboardEvent)
    if event.phase ~= "down" then
        return
    end
    bindVM(self)
    local k = event.key
    if k == 256 then                        -- échap
        setArm(self, false)
    elseif k == 257 or k == 335 then        -- entrée
        commit(self)
    elseif k == 259 then                    -- retour arrière
        if not self.arm then
            return
        end
        if self.text ~= "" then
            -- une lettre : en UTF-8 on retire aussi les octets de continuation
            local i = #self.text
            while i > 1 and bit32.band(string.byte(self.text, i), 0xC0) == 0x80 do
                i -= 1
            end
            self.text = string.sub(self.text, 1, i - 1)
            local p = self.noteText
            if p then
                p.value = self.text
            end
        else
            -- texte vide : on efface le dernier trait
            local n = #self.cur
            if n >= 2 then
                local i = n - 2
                while i > 0 and not (self.cur[i - 1] == -1 and self.cur[i] == -1) do
                    i -= 2
                end
                for _ = 1, n - i do
                    table.remove(self.cur)
                end
            end
        end
    elseif not self.arm then
        -- hors saisie, le clavier pilote la lecture (espace, image par image)
        local c = self.cmd
        if c then
            if k == 32 then
                c.value = 1                 -- lecture / pause
            elseif k == 263 then
                c.value = 2                 -- image précédente
            elseif k == 262 then
                c.value = 3                 -- image suivante
            end
        end
    end
end

local function textEvent(self: Notes, event: TextInput)
    bindVM(self)
    if not self.arm then
        -- Frame.io : on tape, ça ouvre le commentaire à l'image affichée et ça met en pause
        self.editing = 0
        setArm(self, true)
    end
    self.text ..= event.text
    paintText(self)
end

local function circle(path: Path, cx: number, cy: number, r: number)
    local c = 0.5523 * r
    path:reset()
    path:moveTo(Vector.xy(cx + r, cy))
    path:cubicTo(Vector.xy(cx + r, cy + c), Vector.xy(cx + c, cy + r), Vector.xy(cx, cy + r))
    path:cubicTo(Vector.xy(cx - c, cy + r), Vector.xy(cx - r, cy + c), Vector.xy(cx - r, cy))
    path:cubicTo(Vector.xy(cx - r, cy - c), Vector.xy(cx - c, cy - r), Vector.xy(cx, cy - r))
    path:cubicTo(Vector.xy(cx + c, cy - r), Vector.xy(cx + r, cy - c), Vector.xy(cx + r, cy))
    path:close()
end

local function strokes(self: Notes, pts: { number }, renderer: Renderer, paint: Paint)
    self.path:reset()
    local pen = false
    local i = 1
    while i < #pts do
        local x, y = pts[i], pts[i + 1]
        if x == -1 and y == -1 then
            pen = false
        elseif pen then
            self.path:lineTo(Vector.xy(x, y))
        else
            self.path:moveTo(Vector.xy(x, y))
            pen = true
        end
        i += 2
    end
    renderer:drawPath(self.path, paint)
end

local function draw(self: Notes, renderer: Renderer)
    -- les notes déjà posées : leur repère sur la barre, et leur tracé quand la tête est dessus
    local prog = self.progress
    local now = (if prog then prog.value else 0) * self.duration
    for _, n in self.notes do
        local x = self.barX0 + (self.barX1 - self.barX0) * n.t / math.max(self.duration, 0.001)
        local here = math.abs(now - n.t) < 0.25
        local big = here or n.id == self.sel
        circle(self.path, x, self.barY, if big then 11 else 8)
        renderer:drawPath(self.path, self.fills[colorOf(n.c)])                 -- la pastille, couleur de la note
        renderer:drawPath(self.path, if big then self.ringOn else self.ring)   -- son cercle blanc
        if here then
            -- le tracé n'apparaît qu'à l'endroit où la note a été posée (un clic sur la ligne
            -- y emmène la tête de lecture, donc il revient en cliquant la note)
            strokes(self, n.pts, renderer, self.inksOld[colorOf(n.c)])
        end
    end
    -- la note en cours
    if self.arm and #self.cur > 0 then
        strokes(self, self.cur, renderer, self.inks[self.col])
    end
    -- la ligne choisie porte un liseré rouge à gauche (lisible sans couvrir le texte)
    local selRow = 0
    for r = 1, self.rows do
        local k = self.top + r - 1
        if k <= #self.notes and self.notes[k].id == self.sel then
            selRow = r
        end
    end
    if selRow > 0 then
        local ey = self.rowY0 + (selRow - 1) * self.rowH
        local qe = self.path2
        qe:reset()
        qe:moveTo(Vector.xy(self.colX + 16, ey - 20))
        qe:lineTo(Vector.xy(self.colX + 16, ey + 20))
        renderer:drawPath(qe, self.edge)
    end
    -- la colonne : le trait « dessin » près du timecode, et à droite les deux actions de la ligne
    -- (coche = traitée, croix = supprimer) — dessinées ici, la police d'un projet n'a pas ces signes
    local q2 = self.path2
    for r = 1, self.rows do
        local k = self.top + r - 1
        if k <= #self.notes then
            local note = self.notes[k]
            local cy2 = self.rowY0 + (r - 1) * self.rowH
            if #note.pts > 0 then          -- ce petit trait dit : cette note porte un dessin
                q2:reset()
                q2:moveTo(Vector.xy(self.colX + 104, cy2 - 6))
                q2:lineTo(Vector.xy(self.colX + 114, cy2 - 16))
                renderer:drawPath(q2, self.inksOld[colorOf(note.c)])
            end
            local sel = note.id == self.sel
            if note.done or sel then       -- la coche
                local cx2 = self.colX + self.colW - 64
                q2:reset()
                q2:moveTo(Vector.xy(cx2 - 5, cy2))
                q2:lineTo(Vector.xy(cx2 - 1, cy2 + 4.5))
                q2:lineTo(Vector.xy(cx2 + 6, cy2 - 5))
                renderer:drawPath(q2, if note.done then self.okInk else self.dim)
            end
            if sel then                    -- la croix : supprimer
                local cx3 = self.colX + self.colW - 32
                q2:reset()
                q2:moveTo(Vector.xy(cx3 - 5, cy2 - 5))
                q2:lineTo(Vector.xy(cx3 + 5, cy2 + 5))
                q2:moveTo(Vector.xy(cx3 + 5, cy2 - 5))
                q2:lineTo(Vector.xy(cx3 - 5, cy2 + 5))
                renderer:drawPath(q2, self.dim)
            end
        end
    end
    -- le champ : cadre allumé et curseur clignotant quand on écrit (la largeur du texte est estimée :
    -- un script n'a pas les métriques de la police)
    do
        local q3 = self.path2
        local grow = (self.lines - 1) * LINE_H
        local bx, by, bw2, bh2 = self.colX + 24, self.boxY - grow, self.colW - 48, self.boxH + grow
        q3:reset()
        q3:moveTo(Vector.xy(bx, by))
        q3:lineTo(Vector.xy(bx + bw2, by))
        q3:lineTo(Vector.xy(bx + bw2, by + bh2))
        q3:lineTo(Vector.xy(bx, by + bh2))
        q3:close()
        renderer:drawPath(q3, if self.arm then self.edge else self.frame)
    end
    -- la barre d'outils du champ : les quatre couleurs, puis la brosse (une seule, inchangée)
    do
        local q4 = self.path2
        for k = 1, 4 do
            local cx4 = self.dotX + (k - 1) * self.dotGap
            circle(self.path, cx4, self.penY, self.dotR)
            renderer:drawPath(self.path, self.fills[k])
            if k == self.col then                      -- la couleur choisie porte un anneau
                circle(q4, cx4, self.penY, self.dotR + 4)
                renderer:drawPath(q4, self.ring)
            end
        end
    end
    -- le bouton brosse : un disque, plein quand elle dessine
    local r = self.penR
    local cx, cy = self.penX, self.penY
    circle(self.path, cx, cy, r)
    renderer:drawPath(self.path, if self.pen then self.fills[self.col] else self.penRing)
    -- la brosse elle-même (son propre Path : deux drawPath d'un même Path dans une frame se marchent dessus)
    local q = self.path2
    q:reset()
    q:moveTo(Vector.xy(cx - 8, cy + 8))      -- la pointe
    q:lineTo(Vector.xy(cx + 7, cy - 7))      -- le corps
    q:moveTo(Vector.xy(cx - 4, cy + 1))      -- la bague
    q:lineTo(Vector.xy(cx + 1, cy + 5))
    renderer:drawPath(q, if self.pen then self.penInk else self.inks[self.col])
end

return function(): Layout<Notes>
    return {
        duration = 1, filmBottom = 0, barX0 = 0, barX1 = 100, barY = 0, penX = 0, penY = 0, penR = 15,
        dotX = 0, dotGap = 28, dotR = 8, col = 4,
        textX = 0, textBase = 0, fieldW = 300, boxBgH = nil, boxBgY = nil, boxAtY = nil, lines = 1,
        context = late(), bound = false, progress = nil, annOn = nil, noteText = nil, noteHint = nil,
        notes = {}, dirty = true, cur = {}, drawing = false, arm = false, text = "", nextId = 1, t = 0,
        inDown = false, unlock = false,
        cmd = nil, seekTo = nil, selY = nil, selA = nil, hover = 0, phA = nil, noteAt = nil, sendA = nil, noteCount = nil, holdPlay = nil, pen = false, boxY = 1e9, boxH = 0, sendX = 0, sendY = 0, sendW = 0, sendH = 0, rowT = {}, rowX = {}, rowA = {}, sel = 0, editing = 0, top = 1,
        colX = 1e9, colW = 0, rowY0 = 0, rowH = 1, rows = 0, fps = 60, saved = "",
        path = Path.new(),
        path2 = Path.new(),
        inks = {
            Paint.with({ style = "stroke", thickness = 4, cap = "round", join = "round", color = PALETTE[1] }),
            Paint.with({ style = "stroke", thickness = 4, cap = "round", join = "round", color = PALETTE[2] }),
            Paint.with({ style = "stroke", thickness = 4, cap = "round", join = "round", color = PALETTE[3] }),
            Paint.with({ style = "stroke", thickness = 4, cap = "round", join = "round", color = PALETTE[4] }),
        },
        inksOld = {
            Paint.with({ style = "stroke", thickness = 3, cap = "round", join = "round", color = PALETTE_SOFT[1] }),
            Paint.with({ style = "stroke", thickness = 3, cap = "round", join = "round", color = PALETTE_SOFT[2] }),
            Paint.with({ style = "stroke", thickness = 3, cap = "round", join = "round", color = PALETTE_SOFT[3] }),
            Paint.with({ style = "stroke", thickness = 3, cap = "round", join = "round", color = PALETTE_SOFT[4] }),
        },
        fills = {
            Paint.with({ style = "fill", color = PALETTE[1] }),
            Paint.with({ style = "fill", color = PALETTE[2] }),
            Paint.with({ style = "fill", color = PALETTE[3] }),
            Paint.with({ style = "fill", color = PALETTE[4] }),
        },
        okInk = Paint.with({ style = "stroke", thickness = 2.5, cap = "round", join = "round", color = 0xFF34D399 }),
        dim = Paint.with({ style = "stroke", thickness = 2, cap = "round", color = 0xFF6E6475 }),
        ink = Paint.with({ style = "stroke", thickness = 4, cap = "round", join = "round", color = INK }),
        inkOld = Paint.with({ style = "stroke", thickness = 3, cap = "round", join = "round", color = INK_OLD }),
        dot = Paint.with({ style = "fill", color = DOT }),
        ring = Paint.with({ style = "stroke", thickness = 2, color = 0xFFFFFFFF }),
        edge = Paint.with({ style = "stroke", thickness = 3, cap = "round", color = INK }),
        frame = Paint.with({ style = "stroke", thickness = 1, color = 0xFF3A3442 }),
        ringOn = Paint.with({ style = "stroke", thickness = 4, color = 0xFFFFFFFF }),
        penRing = Paint.with({ style = "stroke", thickness = 1.5, color = 0xFF3A3442 }),
        penDisc = Paint.with({ style = "fill", color = INK }),
        penInk = Paint.with({ style = "stroke", thickness = 3, cap = "round", join = "round", color = 0xFF151216 }),
        init = init, advance = advance, resize = resize, draw = draw,
        pointerDown = pointerDown, pointerMove = pointerMove, pointerUp = pointerUp, pointerExit = pointerExit,
        keyboardEvent = keyboardEvent, textEvent = textEvent,
    }
end
