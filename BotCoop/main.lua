local RebeccaCoop = RegisterMod("Rebecca BotCoop", 2)
local game = Game()
local telemetryEnabled = true
local lastRoom = -1

local function safeCall(defaultValue, callback)
    local ok, value = pcall(callback)
    if ok then return value end
    return defaultValue
end

local function collectVisibleItems()
    local items = {}
    for _, entity in ipairs(Isaac.GetRoomEntities()) do
        if entity.Type == EntityType.ENTITY_PICKUP
            and entity.Variant == PickupVariant.PICKUP_COLLECTIBLE
            and entity.SubType > 0 then
            table.insert(items, entity.SubType)
        end
    end
    return items
end

local function collectEnemies()
    local alive = 0
    local hasBoss = false
    for _, entity in ipairs(Isaac.GetRoomEntities()) do
        if entity:IsActiveEnemy(false) and not entity:HasEntityFlags(EntityFlag.FLAG_FRIENDLY) then
            alive = alive + 1
            if entity:IsBoss() then hasBoss = true end
        end
    end
    return alive, hasBoss
end

local function emitTelemetry()
    if not telemetryEnabled or game:GetNumPlayers() < 1 then return end
    local player = Isaac.GetPlayer(0)
    local enemies, hasBoss = collectEnemies()
    local roomIndex = game:GetLevel():GetCurrentRoomIndex()
    local data = {
        version = 2,
        frame = game:GetFrameCount(),
        jugador_hp = player:GetHearts() + player:GetSoulHearts(),
        sala_actual = roomIndex,
        sala_tipo = safeCall(-1, function() return game:GetRoom():GetType() end),
        enemigos_vivos = enemies,
        hay_jefe = hasBoss,
        items_visibles = collectVisibleItems(),
        jugadores = game:GetNumPlayers(),
        coop_activo = game:GetNumPlayers() > 1
    }
    Isaac.DebugString("BOTCOOP_IA_DATOS:" .. json.encode(data))
    lastRoom = roomIndex
end

function RebeccaCoop:OnUpdate()
    if Input.IsButtonTriggered(Keyboard.KEY_K, 0) then
        Isaac.ExecuteCommand("addplayer 8")
        Isaac.DebugString("REBECCA_COOP: segundo personaje solicitado")
    end
    if Input.IsButtonTriggered(Keyboard.KEY_F8, 0) then
        telemetryEnabled = not telemetryEnabled
        Isaac.DebugString("REBECCA_COOP: telemetria=" .. tostring(telemetryEnabled))
    end
    if game:GetFrameCount() % 15 == 0 or game:GetLevel():GetCurrentRoomIndex() ~= lastRoom then
        emitTelemetry()
    end
end

function RebeccaCoop:OnGameStarted()
    lastRoom = -1
    Isaac.DebugString("REBECCA_COOP: listo. K agrega jugador, F8 pausa telemetria")
end

RebeccaCoop:AddCallback(ModCallbacks.MC_POST_UPDATE, RebeccaCoop.OnUpdate)
RebeccaCoop:AddCallback(ModCallbacks.MC_POST_GAME_STARTED, RebeccaCoop.OnGameStarted)
