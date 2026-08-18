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

#ifndef CharacterPackets_h__
#define CharacterPackets_h__

#include "Packet.h"
#include "ObjectGuid.h"
#include "Position.h"
#include <array>
#include <string>
#include <vector>

namespace WorldPackets
{
    namespace Character
    {
        class EnumCharacters final : public ClientPacket
        {
        public:
            EnumCharacters(WorldPacket&& packet) : ClientPacket(CMSG_CHAR_ENUM, std::move(packet)) { }

            void Read() override;
        };

        class EnumCharactersResult final : public ServerPacket
        {
        public:
            struct CharacterInfo
            {
                struct VisualItemInfo
                {
                    uint32 DisplayID = 0;
                    uint32 DisplayEnchantID = 0;
                    uint8 InvType = 0;
                };

                Position PreloadPos;
                ObjectGuid Guid;
                ObjectGuid GuildGUID;
                uint32 Flags = 0;
                uint32 Flags2 = 0;
                int32 MapID = 0;
                uint32 PetCreatureDisplayID = 0;
                uint32 PetCreatureFamilyID = 0;
                uint32 PetExperienceLevel = 0;
                int32 ZoneID = 0;
                uint8 ClassID = 0;
                uint8 ExperienceLevel = 0;
                uint8 FaceID = 0;
                uint8 FacialHair = 0;
                uint8 HairColor = 0;
                uint8 HairStyle = 0;
                uint8 ListPosition = 0;
                uint8 RaceID = 0;
                uint8 SexID = 0;
                uint8 SkinID = 0;
                bool FirstLogin = false;
                std::string Name;
                std::array<VisualItemInfo, 23> VisualItems = { };
            };

            struct RestrictedFactionChangeRuleInfo
            {
                int32 Mask = 0;
                uint8 Race = 0;
            };

            EnumCharactersResult() : ServerPacket(SMSG_CHAR_ENUM) { }

            WorldPacket const* Write() override;

            bool Success = false;
            std::vector<CharacterInfo> Characters;
            std::vector<RestrictedFactionChangeRuleInfo> FactionChangeRestrictions;
        };

        class ShowingCloak final : public ClientPacket
        {
        public:
            ShowingCloak(WorldPacket&& packet) : ClientPacket(CMSG_SHOWING_CLOAK, std::move(packet)) { }

            void Read() override;

            bool ShowCloak = false;
        };

        class ShowingHelm final : public ClientPacket
        {
        public:
            ShowingHelm(WorldPacket&& packet) : ClientPacket(CMSG_SHOWING_HELM, std::move(packet)) { }

            void Read() override;

            bool ShowHelm = false;
        };

        class LogoutRequest final : public ClientPacket
        {
        public:
            LogoutRequest(WorldPacket&& packet) : ClientPacket(std::move(packet)) { }

            void Read() override { }
        };

        class LogoutResponse final : public ServerPacket
        {
        public:
            LogoutResponse() : ServerPacket(SMSG_LOGOUT_RESPONSE, 4 + 1) { }

            WorldPacket const* Write() override;

            uint32 LogoutResult = 0;
            bool Instant = false;
        };

        class LogoutComplete final : public ServerPacket
        {
        public:
            LogoutComplete() : ServerPacket(SMSG_LOGOUT_COMPLETE, 0) { }

            WorldPacket const* Write() override { return &_worldPacket; }
        };

        class LogoutCancel final : public ClientPacket
        {
        public:
            LogoutCancel(WorldPacket&& packet) : ClientPacket(std::move(packet)) { }

            void Read() override { }
        };

        class LogoutCancelAck final : public ServerPacket
        {
        public:
            LogoutCancelAck() : ServerPacket(SMSG_LOGOUT_CANCEL_ACK, 0) { }

            WorldPacket const* Write() override { return &_worldPacket; }
        };

        class PlayerLogin final : public ClientPacket
        {
        public:
            PlayerLogin(WorldPacket&& packet) : ClientPacket(CMSG_PLAYER_LOGIN, std::move(packet)) { }

            void Read() override;

            ObjectGuid Guid;      ///< Guid of the player that is logging in
        };

        class PlayerLogout final : public ClientPacket
        {
        public:
            PlayerLogout(WorldPacket&& packet) : ClientPacket(std::move(packet)) { }

            void Read() override { }
        };

        class PlayedTimeClient final : public ClientPacket
        {
        public:
            PlayedTimeClient(WorldPacket&& packet) : ClientPacket(CMSG_PLAYED_TIME, std::move(packet)) { }

            void Read() override;

            bool TriggerScriptEvent = false;
        };

        class PlayedTime final : public ServerPacket
        {
        public:
            PlayedTime() : ServerPacket(SMSG_PLAYED_TIME, 9) { }

            WorldPacket const* Write() override;

            uint32 TotalTime = 0;
            uint32 LevelTime = 0;
            bool TriggerScriptEvent = false;
        };

        // Sent once, immediately after the player is added to its map (never before);
        // field order is int32 MapID, then Position X/Y/Z/O, matching the pinned
        // Cataclysm reference and this fork's existing raw writer.
        class LoginVerifyWorld final : public ServerPacket
        {
        public:
            LoginVerifyWorld() : ServerPacket(SMSG_LOGIN_VERIFY_WORLD, 20) { }

            WorldPacket const* Write() override;

            int32 MapID = 0;
            Position Pos;
        };
    }
}

#endif // CharacterPackets_h__
