/*
 * This file is part of the AzerothCore Project. See AUTHORS file for Copyright information
 *
 * This program is free software; you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the
 * Free Software Foundation; either version 2 of the License, or (at your
 * option) any later version.
 *
 * This program is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 * FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
 * more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program. If not, see <http://www.gnu.org/licenses/>.
 */

#include "CreatureScript.h"
#include "EventMap.h"
#include "ScriptedCreature.h"

namespace Mulgore
{
    enum RiteOfHonor
    {
        // Texts
        SAY_AGGRO = 0,

        // Events
        EVENT_QUILLHORN     = 1,
        EVENT_TORCH_TOSS    = 2,

        // Spells
        SPELL_QUILLHORN     = 81691,
        SPELL_TORCH_TOSS    = 81695,
        SPELL_IMMOLATION    = 69316
    };

    // Ported from TrinityCore 4.3.4 (pinned c6992177) zone_mulgore.cpp (#211).
    struct npc_mulgore_chief_squealer_thornmantle : public ScriptedAI
    {
        npc_mulgore_chief_squealer_thornmantle(Creature* creature) : ScriptedAI(creature) { }

        void JustEngagedWith(Unit* who) override
        {
            Talk(SAY_AGGRO, who);
            _events.ScheduleEvent(EVENT_QUILLHORN, 10s);
        }

        void EnterEvadeMode(EvadeReason why) override
        {
            ScriptedAI::EnterEvadeMode(why);
            _events.Reset();
        }

        void JustDied(Unit* killer) override
        {
            ScriptedAI::JustDied(killer);
            _events.Reset();
            me->m_Events.AddEventAtOffset([&]() { DoCastSelf(SPELL_IMMOLATION); }, 4s);
        }

        void UpdateAI(uint32 diff) override
        {
            if (!UpdateVictim())
                return;

            _events.Update(diff);

            if (me->HasUnitState(UNIT_STATE_CASTING))
                return;

            while (uint32 eventId = _events.ExecuteEvent())
            {
                switch (eventId)
                {
                    case EVENT_QUILLHORN:
                        DoCastVictim(SPELL_QUILLHORN);
                        _events.ScheduleEvent(EVENT_TORCH_TOSS, 2s + 500ms);
                        _events.Repeat(60s);
                        break;
                    case EVENT_TORCH_TOSS:
                        DoCastVictim(SPELL_TORCH_TOSS);
                        break;
                    default:
                        break;
                }

                if (me->HasUnitState(UNIT_STATE_CASTING))
                    return;
            }

            DoMeleeAttackIfReady();
        }

    private:
        EventMap _events;
    };
}

void AddSC_mulgore()
{
    using namespace Mulgore;
    RegisterCreatureAI(npc_mulgore_chief_squealer_thornmantle);
}
