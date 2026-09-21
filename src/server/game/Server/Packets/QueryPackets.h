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

#ifndef QueryPackets_h__
#define QueryPackets_h__

#include "CreatureData.h"
#include "Packet.h"
#include "Unit.h"
#include <array>

namespace WorldPackets
{
    namespace Query
    {
        class HotfixNotifyBlob final : public ServerPacket
        {
        public:
            struct Record
            {
                uint32 TableHash = 0;
                uint32 Timestamp = 0;
                int32 Entry = 0;
            };

            HotfixNotifyBlob() : ServerPacket(SMSG_HOTFIX_NOTIFY_BLOB, 3) { }

            WorldPacket const* Write() override;

            std::vector<Record> Hotfixes;
        };

        class NameQuery final : public ClientPacket
        {
        public:
            NameQuery(WorldPacket&& packet) : ClientPacket(CMSG_NAME_QUERY, std::move(packet)) {}

            void Read() override;

            ObjectGuid Guid;
        };

        class NameQueryResponse final : public ServerPacket
        {
        public:
            NameQueryResponse() : ServerPacket(SMSG_NAME_QUERY_RESPONSE, 8 + 1 + 1 + 1 + 1 + 1 + 10) {}

            WorldPacket const* Write() override;

            PackedGuid Guid;
            uint8 NameUnknown = false;
            std::string_view Name;
            std::string_view RealmName = ""; // Only set for cross realm interaction (such as Battlegrounds)
            uint8 Race = RACE_NONE;
            uint8 Sex = GENDER_MALE;
            uint8 Class = CLASS_NONE;
            uint8 Declined = false;
            DeclinedName DeclinedNames;
        };

        class TimeQuery final : public ClientPacket
        {
        public:
            TimeQuery(WorldPacket&& packet) : ClientPacket(CMSG_QUERY_TIME, std::move(packet)) {}

            void Read() override {};
        };

        class TimeQueryResponse final : public ServerPacket
        {
        public:
            TimeQueryResponse() : ServerPacket(SMSG_QUERY_TIME_RESPONSE, 4 + 4) {}

            WorldPacket const* Write() override;

            uint32 ServerTime;
            uint32 TimeResponse;
        };

        struct CreatureStats
        {
            std::array<std::string, 4> Name = {};
            std::array<std::string, 4> NameAlt = {};
            std::string Title;
            std::string CursorName;
            std::array<uint32, 2> Flags = {};
            uint32 CreatureType = 0;
            uint32 CreatureFamily = 0;
            uint32 Classification = 0;
            std::array<uint32, MAX_KILL_CREDIT> ProxyCreatureID = {};
            std::array<uint32, 4> CreatureDisplayID = {};
            float HpMulti = 0.0f;
            float EnergyMulti = 0.0f;
            bool Leader = false;
            std::array<uint32, MAX_CREATURE_QUEST_ITEMS> QuestItems = {};
            uint32 CreatureMovementInfoID = 0;
            uint32 RequiredExpansion = 0;
        };

        class QueryCreatureResponse final : public ServerPacket
        {
        public:
            QueryCreatureResponse() : ServerPacket(SMSG_CREATURE_QUERY_RESPONSE, 100) { }

            WorldPacket const* Write() override;

            bool Allow = false;
            CreatureStats Stats;
            uint32 CreatureID = 0;
        };

        class CorpseMapPositionQuery final : public ClientPacket
        {
        public:
            CorpseMapPositionQuery(WorldPacket&& packet) : ClientPacket(CMSG_CORPSE_MAP_POSITION_QUERY, std::move(packet)) {}

            void Read() override;

            uint32 unk;
        };
    }
}

#endif // QueryPackets_h__
