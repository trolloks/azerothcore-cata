/*
 * This file is part of the AzerothCore Project. See AUTHORS file for Copyright information
 *
 * This program is free software; you can redistribute it and/or modify it under
 * the terms of the GNU General Public License as published by the Free Software
 * Foundation; either version 2 of the License, or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
 * FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along with
 * this program. If not, see <http://www.gnu.org/licenses/>.
 */

#include "TestMap.h"
#include "DBCStores.h"
#include "ScriptMgr.h"
#include "ScriptDefines/AllMapScript.h"
#include "ScriptDefines/AllSpellScript.h"
#include "ScriptDefines/GlobalScript.h"
#include "ScriptDefines/MiscScript.h"
#include "ScriptDefines/PlayerScript.h"
#include "ScriptDefines/UnitScript.h"
#include "ScriptDefines/WorldObjectScript.h"
#include "ScriptDefines/CommandScript.h"
#include "ScriptDefines/ServerScript.h"
#include "ScriptDefines/SessionScript.h"

TestMap::TestMap()
    : Map(0, 0, REGULAR_DIFFICULTY, nullptr)
{
    _fakeMapEntry = {};
    _fakeMapEntry.map_type = MAP_COMMON;
    _fakeMapEntry.MapID = 0;
    const_cast<MapEntry const*&>(i_mapEntry) = &_fakeMapEntry;
}

TestMap::~TestMap()
{
}

/*static*/ void TestMap::EnsureDBC()
{
    static bool initialized = false;
    if (initialized)
        return;
    initialized = true;

    // Native build-15595 ChrClassesXPowerTypes.dbc order defines the visible slots.
    uint32 powerId = 0;
    for (auto const& [classId, power] : {
        std::pair<uint32, uint32>{ 1, 1 }, { 1, 10 }, { 2, 0 }, { 2, 9 }, { 2, 10 },
        { 3, 2 }, { 3, 10 }, { 4, 3 }, { 4, 10 }, { 5, 0 }, { 5, 10 },
        { 6, 6 }, { 6, 10 }, { 7, 0 }, { 7, 10 }, { 8, 0 }, { 8, 10 },
        { 9, 0 }, { 9, 7 }, { 9, 10 }, { 11, 0 }, { 11, 1 }, { 11, 3 }, { 11, 8 }, { 11, 10 } })
    {
        if (!sChrPowerTypesStore.LookupEntry(powerId))
            sChrPowerTypesStore.SetEntry(powerId, new ChrPowerTypesEntry{ powerId, classId, power });
        ++powerId;
    }

    // Insert a fake MapEntry so Map constructor doesn't crash
    if (!sMapStore.LookupEntry(0))
    {
        auto* entry = new MapEntry{};
        entry->MapID = 0;
        entry->map_type = MAP_COMMON;
        entry->entrance_map = -1;
        sMapStore.SetEntry(0, entry);
    }

    // Initialize all script registries so CALL_ENABLED_HOOKS doesn't
    // crash on uninitialized vectors during Object/Unit/Map operations
    ScriptRegistry<AllMapScript>::InitEnabledHooksIfNeeded(ALLMAPHOOK_END);
    ScriptRegistry<AllSpellScript>::InitEnabledHooksIfNeeded(ALLSPELLHOOK_END);
    ScriptRegistry<GlobalScript>::InitEnabledHooksIfNeeded(GLOBALHOOK_END);
    ScriptRegistry<MiscScript>::InitEnabledHooksIfNeeded(MISCHOOK_END);
    ScriptRegistry<PlayerScript>::InitEnabledHooksIfNeeded(PLAYERHOOK_END);
    ScriptRegistry<UnitScript>::InitEnabledHooksIfNeeded(UNITHOOK_END);
    ScriptRegistry<WorldObjectScript>::InitEnabledHooksIfNeeded(WORLDOBJECTHOOK_END);
    ScriptRegistry<CommandSC>::InitEnabledHooksIfNeeded(ALLCOMMANDHOOK_END);
    ScriptRegistry<ServerScript>::InitEnabledHooksIfNeeded(SERVERHOOK_END);
    ScriptRegistry<SessionScript>::InitEnabledHooksIfNeeded(SESSIONHOOK_END);
}
