--!strict
-- ReviewPlayer: review player for a Rive CLI film. Loop, play / pause, a playhead you
-- click or drag, counter, soundtrack re-synced to the position.
--
-- Contract (see README.md): the whole film is a function of ONE number, the `time` (fraction 0..1) of the
-- NestedRemapAnimation of the film nested in the review artboard, bound to the ViewModel property `progress`.
-- This script writes `progress` every frame, plus what the bar displays: `headX` (x of the head), `timeText`,
-- `playOp` / `pauseOp` (icon opacity). It receives all the artboard's pointer events:
--   bar (between barY0 and barY1)         -> click = go there, drag = scrub (playback resumes on release)
--   button (circle btnX, btnY, btnR)      -> play / pause
--   film frame (above filmBottom)         -> play / pause
-- Sound: AudioAsset named `audio` (empty = muted). Audio.play returns nil in headless mode (--screenshot): everything stays
-- silent and the rest works.

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
    self.annOn = vm:getNumber("annOn")        -- 1 = the pencil is drawing: the player releases the pointer
    self.holdPlay = vm:getNumber("holdPlay")  -- 1 = a note is being written: the film stays frozen
    self.seekTo = vm:getNumber("seekTo")      -- the time of a note: go there, then -1
    self.cmd = vm:getNumber("cmd")            -- 1 = play/pause, 2 = frame -1, 3 = frame +1
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
        -- going to a note means landing on ITS frame: we stop there (the note's drawing
        -- is only visible at that instant, it would be gone in half a second if playback continued)
        stopSound(self)
        self.playing = false
        self.dragging = false
        self.wasPlaying = false
    end
    -- in annotation mode the film is frozen: the note belongs to the frame being looked at
    local hp = self.holdPlay
    local a = self.annOn
    if (a ~= nil and a.value >= 0.5) or (hp ~= nil and hp.value >= 0.5) then
        if self.playing or self.sound ~= nil then
            stopSound(self)          -- not a pause: the track is released, it can no longer run away on its own
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

-- annotation mode (ReviewNotes) takes over the pointer: the player neither scrubs nor toggles.
-- WRITING counts as much as DRAWING: without `holdPlay` here, the click leaving the field (on the frame,
-- on the bar) also reached the player, which restarted the film, so the posted note seemed to vanish.
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
        self.wasPlaying = false      -- we are annotating: nothing restarts on release
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
