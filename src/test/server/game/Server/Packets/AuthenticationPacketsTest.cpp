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

#include "AuthenticationPackets.h"
#include "SharedDefines.h"
#include "Util.h"
#include <gtest/gtest.h>
#include <algorithm>
#include <numeric>
#include <span>
#include <string_view>
#include <vector>

namespace
{
    std::vector<uint8> HexBytes(std::string_view hex)
    {
        std::vector<uint8> bytes(hex.size() / 2);
        Acore::Impl::HexStrToByteArray(hex, bytes.data(), bytes.size());
        return bytes;
    }

    WorldPacket MakeAuthSessionPacket(std::vector<uint8> const& payload)
    {
        WorldPacket packet(CMSG_AUTH_SESSION, payload.size());
        packet.append(payload.data(), payload.size());
        return packet;
    }

    std::string PayloadHex(ByteBuffer const* packet)
    {
        return ByteArrayToHexStr(std::span<uint8 const>(packet->contents(), packet->size()));
    }

    void SetSuccessInfo(WorldPackets::Auth::AuthResponse& response, uint8 activeExpansion = 3, uint8 accountExpansion = 3)
    {
        WorldPackets::Auth::AuthSuccessInfo& success = response.SuccessInfo.emplace();
        success.ActiveExpansionLevel = activeExpansion;
        success.AccountExpansionLevel = accountExpansion;
    }
}

TEST(AuthenticationPacketsTest, WritesAuthChallenge)
{
    WorldPackets::Auth::AuthChallenge challenge;
    std::iota(challenge.DosChallenge.begin(), challenge.DosChallenge.end(), uint8(0));
    challenge.Challenge = { 0xAA, 0xBB, 0xCC, 0xDD };

    EXPECT_EQ(PayloadHex(challenge.Write()),
        "000102030405060708090A0B0C0D0E0F101112131415161718191A1B1C1D1E1FAABBCCDD01");
}

TEST(AuthenticationPacketsTest, ReadsAuthSession)
{
    std::vector<uint8> payload = HexBytes(
        "feffffff44332211ff88e5c2970807060504030201670bbe08323706eb3c070200000000e0ca0c290c010203043b01000000cd9c02000000a1b2000841");
    WorldPackets::Auth::AuthSession session(MakeAuthSessionPacket(payload));
    session.Read();

    EXPECT_EQ(session.LoginServerID, -2);
    EXPECT_EQ(session.BattlegroupID, 0x11223344u);
    EXPECT_EQ(session.LoginServerType, -1);
    EXPECT_EQ(session.DosResponse, 0x0102030405060708u);
    EXPECT_EQ(session.Build, 15595);
    EXPECT_EQ(session.RealmID, 2u);
    EXPECT_EQ(session.BuildType, 0);
    EXPECT_EQ(session.LocalChallenge, (std::array<uint8, 4>{ 1, 2, 3, 4 }));
    EXPECT_EQ(session.RegionID, 1u);
    EXPECT_EQ(session.Digest, HexStrToByteArray<20>("0c293b060897ca32070b880cc29ccd6737e0e5be"));
    EXPECT_EQ(PayloadHex(&session.AddonInfo), "A1B2");
    EXPECT_FALSE(session.UseIPv6);
    EXPECT_EQ(session.Account, "A");
    EXPECT_EQ(session.GetRawPacket()->rpos(), session.GetRawPacket()->size());
}

TEST(AuthenticationPacketsTest, RejectsMalformedAuthSession)
{
    std::vector<uint8> valid = HexBytes(
        "feffffff44332211ff88e5c2970807060504030201670bbe08323706eb3c070200000000e0ca0c290c010203043b01000000cd9c02000000a1b2000841");

    auto expectMalformed = [](std::vector<uint8> const& payload)
    {
        WorldPackets::Auth::AuthSession session(MakeAuthSessionPacket(payload));
        EXPECT_THROW(session.Read(), ByteBufferException);
    };

    expectMalformed(std::vector<uint8>(valid.begin(), valid.begin() + 20));

    std::vector<uint8> oversizedAddon = valid;
    oversizedAddon[52] = 0xFF;
    oversizedAddon[53] = 0x00;
    oversizedAddon[54] = 0x00;
    oversizedAddon[55] = 0x00;
    expectMalformed(oversizedAddon);

    std::vector<uint8> oversizedAccount = valid;
    oversizedAccount[59] = 0x10;
    expectMalformed(oversizedAccount);

    std::vector<uint8> trailing = valid;
    trailing.push_back(0);
    expectMalformed(trailing);
}

TEST(AuthenticationPacketsTest, KeepsAllTwelveAccountLengthBits)
{
    std::vector<uint8> payload = HexBytes(
        "feffffff44332211ff88e5c2970807060504030201670bbe08323706eb3c070200000000e0ca0c290c010203043b01000000cd9c02000000a1b2");
    payload.push_back(0x09);
    payload.push_back(0x60);
    payload.insert(payload.end(), 300, 'A');

    WorldPackets::Auth::AuthSession session(MakeAuthSessionPacket(payload));
    session.Read();
    EXPECT_EQ(session.Account.size(), 300u);
    EXPECT_TRUE(std::all_of(session.Account.begin(), session.Account.end(), [](char value) { return value == 'A'; }));
    EXPECT_EQ(session.GetRawPacket()->rpos(), session.GetRawPacket()->size());

    payload.pop_back();
    WorldPackets::Auth::AuthSession truncated(MakeAuthSessionPacket(payload));
    EXPECT_THROW(truncated.Read(), ByteBufferException);
}

TEST(AuthenticationPacketsTest, WritesAuthResponses)
{
    WorldPackets::Auth::AuthResponse error(AUTH_FAILED);
    EXPECT_EQ(PayloadHex(error.Write()), "000D");

    WorldPackets::Auth::AuthResponse success(AUTH_OK);
    SetSuccessInfo(success);
    EXPECT_EQ(PayloadHex(success.Write()), "400000000003000000000300000000000C");

    WorldPackets::Auth::AuthResponse queuedSuccess(AUTH_OK);
    SetSuccessInfo(queuedSuccess);
    queuedSuccess.WaitInfo.emplace(WorldPackets::Auth::AuthWaitInfo{ 7, false });
    EXPECT_EQ(PayloadHex(queuedSuccess.Write()), "A00000000003000000000300000000000C07000000");

    WorldPackets::Auth::AuthResponse queuedError(AUTH_FAILED);
    queuedError.WaitInfo.emplace(WorldPackets::Auth::AuthWaitInfo{ 7, false });
    EXPECT_EQ(PayloadHex(queuedError.Write()), "800D07000000");

    WorldPackets::Auth::AuthResponse queueRefresh(AUTH_WAIT_QUEUE);
    queueRefresh.WaitInfo.emplace(WorldPackets::Auth::AuthWaitInfo{ 7, false });
    EXPECT_EQ(PayloadHex(queueRefresh.Write()), "801B07000000");

    WorldPackets::Auth::AuthResponse distinctExpansions(AUTH_OK);
    SetSuccessInfo(distinctExpansions, 3, 2);
    EXPECT_EQ(PayloadHex(distinctExpansions.Write()), "400000000003000000000200000000000C");
}
