/*
 * This file is part of the AzerothCore Project. See AUTHORS file for Copyright information
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 * FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
 * more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program. If not, see <http://www.gnu.org/licenses/>.
 */

#include "QueryPackets.h"

WorldPacket const* WorldPackets::Query::HotfixNotifyBlob::Write()
{
    _worldPacket.WriteBits(Hotfixes.size(), 22);
    _worldPacket.FlushBits();

    for (Record const& hotfix : Hotfixes)
    {
        _worldPacket << hotfix.TableHash;
        _worldPacket << hotfix.Timestamp;
        _worldPacket << hotfix.Entry;
    }

    return &_worldPacket;
}

void WorldPackets::Query::NameQuery::Read()
{
    _worldPacket >> Guid;
}

WorldPacket const* WorldPackets::Query::NameQueryResponse::Write()
{
    _worldPacket << Guid;
    _worldPacket << NameUnknown;
    if (NameUnknown)
        return &_worldPacket;

    _worldPacket << Name;
    _worldPacket << RealmName;
    _worldPacket << Race;
    _worldPacket << Sex;
    _worldPacket << Class;
    _worldPacket << Declined;
    if (Declined)
    {
        for (uint8 i = 0; i < MAX_DECLINED_NAME_CASES; ++i)
            _worldPacket << DeclinedNames.name[i];
    }

    return &_worldPacket;
}

WorldPacket const* WorldPackets::Query::QueryCreatureResponse::Write()
{
    _worldPacket << uint32(CreatureID | (Allow ? 0x00000000 : 0x80000000)); // creature entry

    if (Allow)
    {
        for (std::string const& name : Stats.Name)
            _worldPacket << name;

        for (std::string const& nameAlt : Stats.NameAlt)
            _worldPacket << nameAlt;

        _worldPacket << Stats.Title;
        _worldPacket << Stats.CursorName;                                     // "Directions" for guard, string for Icons 2.3.0
        _worldPacket.append(Stats.Flags.data(), Stats.Flags.size());          // flags
        _worldPacket << uint32(Stats.CreatureType);                           // CreatureType.dbc
        _worldPacket << uint32(Stats.CreatureFamily);                         // CreatureFamily.dbc
        _worldPacket << uint32(Stats.Classification);                         // Creature Rank (elite, boss, etc)
        _worldPacket.append(Stats.ProxyCreatureID.data(), Stats.ProxyCreatureID.size());
        _worldPacket.append(Stats.CreatureDisplayID.data(), Stats.CreatureDisplayID.size()); // Modelid1-4
        _worldPacket << float(Stats.HpMulti);                                 // dmg/hp modifier
        _worldPacket << float(Stats.EnergyMulti);                             // dmg/mana modifier
        _worldPacket << uint8(Stats.Leader);
        _worldPacket.append(Stats.QuestItems.data(), Stats.QuestItems.size());
        _worldPacket << uint32(Stats.CreatureMovementInfoID);                 // CreatureMovementInfo.dbc
        _worldPacket << uint32(Stats.RequiredExpansion);
    }

    return &_worldPacket;
}

WorldPacket const* WorldPackets::Query::TimeQueryResponse::Write()
{
    _worldPacket << ServerTime;
    _worldPacket << TimeResponse;

    return &_worldPacket;
}

void WorldPackets::Query::CorpseMapPositionQuery::Read()
{
    _worldPacket >> unk;
}
