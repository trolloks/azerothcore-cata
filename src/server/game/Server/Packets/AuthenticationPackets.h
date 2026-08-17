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

#ifndef AuthenticationPackets_h__
#define AuthenticationPackets_h__

#include "Packet.h"
#include <array>
#include <optional>

namespace WorldPackets
{
    namespace Auth
    {
        class AuthChallenge final : public ServerPacket
        {
        public:
            AuthChallenge() : ServerPacket(SMSG_AUTH_CHALLENGE, 37) { }

            WorldPacket const* Write() override;

            std::array<uint8, 32> DosChallenge = {};
            std::array<uint8, 4> Challenge = {};
            uint8 DosZeroBits = 1;
        };

        class AuthSession final : public ClientPacket
        {
        public:
            explicit AuthSession(WorldPacket&& packet) : ClientPacket(CMSG_AUTH_SESSION, std::move(packet)) { }

            void Read() override;

            int32 LoginServerID = 0;
            uint32 BattlegroupID = 0;
            int8 LoginServerType = 0;
            uint64 DosResponse = 0;
            uint16 Build = 0;
            uint32 RealmID = 0;
            int8 BuildType = 0;
            std::array<uint8, 4> LocalChallenge = {};
            uint32 RegionID = 0;
            std::array<uint8, 20> Digest = {};
            ByteBuffer AddonInfo;
            bool UseIPv6 = false;
            std::string Account;
        };

        struct AuthSuccessInfo
        {
            uint32 TimeRemain = 0;
            uint8 ActiveExpansionLevel = 0;
            uint32 TimeSecondsUntilPCKick = 0;
            uint8 AccountExpansionLevel = 0;
            uint32 TimeRested = 0;
            uint8 TimeOptions = 0;
        };

        struct AuthWaitInfo
        {
            uint32 WaitCount = 0;
            bool HasFCM = false;
        };

        class AuthResponse final : public ServerPacket
        {
        public:
            explicit AuthResponse(uint8 result) : ServerPacket(SMSG_AUTH_RESPONSE, 21), Result(result) { }

            WorldPacket const* Write() override;

            std::optional<AuthSuccessInfo> SuccessInfo;
            std::optional<AuthWaitInfo> WaitInfo;
            uint8 Result;
        };
    }
}

#endif // AuthenticationPackets_h__
