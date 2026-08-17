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
#include "AccountScript.h"
#include "AsyncCallbackProcessor.h"
#include "CryptoHash.h"
#include "DatabaseEnv.h"
#include "MySQLThreading.h"
#include "OpenSSLCrypto.h"
#include "Realm.h"
#include "SharedDefines.h"
#include "ScriptMgr.h"
#include "ServerScript.h"
#include "Util.h"
#include "World.h"
#include "WorldMock.h"
#include "WorldSessionMgr.h"
#include "WorldSocket.h"
#include "WorldScript.h"
#include <gmock/gmock.h>
#include <gtest/gtest.h>
#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <numeric>
#include <span>
#include <thread>
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

class WorldAuthenticationHandoffTest : public ::testing::Test
{
protected:
    void SetUp() override
    {
        OpenSSLCrypto::threadsSetup();
        ScriptRegistry<AccountScript>::InitEnabledHooksIfNeeded(ACCOUNTHOOK_END);
        ScriptRegistry<ServerScript>::InitEnabledHooksIfNeeded(SERVERHOOK_END);
        ScriptRegistry<WorldScript>::InitEnabledHooksIfNeeded(WORLDHOOK_END);
    }

    void TearDown() override
    {
        if (_previousWorld)
            sWorld = std::move(_previousWorld);
        if (_mysqlInitialized)
        {
            CharacterDatabase.Close();
            LoginDatabase.Close();
            MySQL::Library_End();
        }
        OpenSSLCrypto::threadsCleanup();
    }

    struct CapturedPacket
    {
        uint32 Opcode;
        std::string Payload;
        bool Encrypted;
    };

    struct SocketPair
    {
        std::shared_ptr<WorldSocket> Server;
        IoContextTcpSocket Client;
    };

    SocketPair MakeSocket()
    {
        tcp::acceptor acceptor(_ioContext, tcp::endpoint(boost::asio::ip::address_v4::loopback(), 0));
        IoContextTcpSocket client(_ioContext);
        client.connect(acceptor.local_endpoint());
        IoContextTcpSocket server(_ioContext);
        acceptor.accept(server);
        return { std::make_shared<WorldSocket>(std::move(server)), std::move(client) };
    }

    PreparedQueryResult QueryAccount(std::string const& account)
    {
        PreparedQueryResult result;
        bool ready = false;
        QueryCallbackProcessor processor;
        LoginDatabasePreparedStatement* statement = LoginDatabase.GetPreparedStatement(LOGIN_SEL_ACCOUNT_INFO_BY_NAME);
        statement->SetData(0, int32(realm.Id.Realm));
        statement->SetData(1, account);
        processor.AddCallback(LoginDatabase.AsyncQuery(statement).WithPreparedCallback(
            [&result, &ready](PreparedQueryResult queryResult)
        {
            result = std::move(queryResult);
            ready = true;
        }));

        for (uint32 attempt = 0; attempt < 5000 && !ready; ++attempt)
        {
            processor.ProcessReadyCallbacks();
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }

        EXPECT_TRUE(ready);
        return result;
    }

    std::shared_ptr<WorldPackets::Auth::AuthSession> MakeSession(std::string const& account, uint32 realmId,
        SessionKey const& key, bool corruptDigest = false)
    {
        WorldPacket raw(CMSG_AUTH_SESSION, 0);
        auto session = std::make_shared<WorldPackets::Auth::AuthSession>(std::move(raw));
        session->Account = account;
        session->Build = 15595;
        session->RealmID = realmId;
        session->LocalChallenge = { 0x10, 0x20, 0x30, 0x40 };

        uint8 zero[4] = {};
        Acore::Crypto::SHA1 digest;
        digest.UpdateData(session->Account);
        digest.UpdateData(zero);
        digest.UpdateData(session->LocalChallenge);
        digest.UpdateData(_authSeed);
        digest.UpdateData(key);
        digest.Finalize();
        session->Digest = digest.GetDigest();
        if (corruptDigest)
            session->Digest[0] ^= 0xFF;

        return session;
    }

    void Invoke(std::shared_ptr<WorldSocket> const& socket,
        std::shared_ptr<WorldPackets::Auth::AuthSession> const& session, PreparedQueryResult result)
    {
        socket->_authSeed = _authSeed;
        socket->HandleAuthSessionCallback(session, std::move(result));
    }

    std::vector<CapturedPacket> Drain(WorldSocket& socket)
    {
        std::vector<CapturedPacket> packets;
        EncryptableAndCompressiblePacket* packet = nullptr;
        while (socket._bufferQueue.Dequeue(packet))
        {
            std::string payload = ByteArrayToHexStr(
                std::span<uint8 const>(packet->contents(), packet->size()));
            packets.push_back({ packet->GetOpcode(), std::move(payload), packet->NeedsEncryption() });
            delete packet;
        }
        return packets;
    }

    static CapturedPacket const* FindAuthResponse(std::vector<CapturedPacket> const& packets)
    {
        auto itr = std::find_if(packets.begin(), packets.end(), [](CapturedPacket const& packet)
        {
            return packet.Opcode == SMSG_AUTH_RESPONSE;
        });
        return itr == packets.end() ? nullptr : &*itr;
    }

    static bool HasSession(WorldSocket const& socket) { return socket._worldSession != nullptr; }
    static bool IsAuthenticated(WorldSocket const& socket) { return socket._authed; }

    boost::asio::io_context _ioContext;
    std::array<uint8, 4> const _authSeed = { 0x01, 0x02, 0x03, 0x04 };
    std::unique_ptr<IWorld> _previousWorld;
    bool _mysqlInitialized = false;
};

TEST_F(WorldAuthenticationHandoffTest, UsesPersistedAuthserverKey)
{
    char const* loginInfo = std::getenv("AC_PLAN6_LOGIN_DATABASE_INFO");
    char const* characterInfo = std::getenv("AC_PLAN6_CHARACTER_DATABASE_INFO");
    char const* accountValue = std::getenv("AC_PLAN6_ACCOUNT");
    char const* accountIdValue = std::getenv("AC_PLAN6_ACCOUNT_ID");
    char const* realmIdValue = std::getenv("AC_PLAN6_REALM_ID");
    char const* sessionKeyValue = std::getenv("AC_PLAN6_SESSION_KEY_HEX");
    if (!loginInfo || !characterInfo || !accountValue || !accountIdValue || !realmIdValue || !sessionKeyValue)
        GTEST_SKIP() << "Plan 6 disposable database environment is not configured";

    uint32 accountId = uint32(std::stoul(accountIdValue));
    uint32 realmId = uint32(std::stoul(realmIdValue));
    ASSERT_EQ(std::string_view(sessionKeyValue).size(), SESSION_KEY_LENGTH * 2);
    SessionKey expectedKey = HexStrToByteArray<SESSION_KEY_LENGTH>(sessionKeyValue);

    MySQL::Library_Init();
    _mysqlInitialized = true;
    LoginDatabase.SetConnectionInfo(loginInfo, 1, 1);
    CharacterDatabase.SetConnectionInfo(characterInfo, 1, 1);
    ASSERT_EQ(LoginDatabase.Open(), 0u);
    ASSERT_EQ(CharacterDatabase.Open(), 0u);
    ASSERT_TRUE(LoginDatabase.PrepareStatements());
    ASSERT_TRUE(CharacterDatabase.PrepareStatements());

    _previousWorld = std::move(sWorld);
    auto world = std::make_unique<::testing::NiceMock<WorldMock>>();
    ON_CALL(*world, IsClosed()).WillByDefault(::testing::Return(false));
    ON_CALL(*world, GetPlayerSecurityLimit()).WillByDefault(::testing::Return(SEC_PLAYER));
    ON_CALL(*world, GetAvailableDbcLocale(::testing::_)).WillByDefault(::testing::Return(LOCALE_enUS));
    ON_CALL(*world, getBoolConfig(::testing::_)).WillByDefault(::testing::Return(false));
    ON_CALL(*world, getIntConfig(::testing::_)).WillByDefault(::testing::Return(0));
    ON_CALL(*world, getIntConfig(CONFIG_EXPANSION)).WillByDefault(::testing::Return(3));
    ON_CALL(*world, getIntConfig(CONFIG_SOCKET_TIMEOUTTIME)).WillByDefault(::testing::Return(60000));
    ON_CALL(*world, getIntConfig(CONFIG_SOCKET_TIMEOUTTIME_ACTIVE)).WillByDefault(::testing::Return(60000));
    sWorld = std::move(world);
    realm.Id.Realm = realmId;
    sWorldSessionMgr->SetPlayerAmountLimit(0);

    std::vector<std::string> transcript;

    SocketPair unknownSocket = MakeSocket();
    auto unknownSession = MakeSession("PLAN6_MISSING", realmId, expectedKey);
    Invoke(unknownSocket.Server, unknownSession, QueryAccount(unknownSession->Account));
    std::vector<CapturedPacket> unknownPackets = Drain(*unknownSocket.Server);
    CapturedPacket const* unknown = FindAuthResponse(unknownPackets);
    ASSERT_NE(unknown, nullptr);
    EXPECT_EQ(unknown->Payload, "0015");
    EXPECT_FALSE(unknown->Encrypted);
    EXPECT_FALSE(unknownSocket.Server->IsOpen());
    EXPECT_FALSE(HasSession(*unknownSocket.Server));
    transcript.push_back("unknown=0015:plain:closed");

    PreparedQueryResult accountResult = QueryAccount(accountValue);
    ASSERT_TRUE(accountResult);
    SessionKey databaseKey = accountResult->Fetch()[1].Get<Binary, SESSION_KEY_LENGTH>();
    EXPECT_EQ(databaseKey, expectedKey);

    SocketPair realmSocket = MakeSocket();
    auto realmSession = MakeSession(accountValue, realmId + 1, databaseKey);
    Invoke(realmSocket.Server, realmSession, QueryAccount(accountValue));
    std::vector<CapturedPacket> realmPackets = Drain(*realmSocket.Server);
    CapturedPacket const* wrongRealm = FindAuthResponse(realmPackets);
    ASSERT_NE(wrongRealm, nullptr);
    EXPECT_EQ(wrongRealm->Payload, "0027");
    EXPECT_TRUE(wrongRealm->Encrypted);
    EXPECT_FALSE(realmSocket.Server->IsOpen());
    EXPECT_FALSE(HasSession(*realmSocket.Server));
    transcript.push_back("wrong-realm=0027:encrypted:closed");

    SocketPair digestSocket = MakeSocket();
    auto digestSession = MakeSession(accountValue, realmId, databaseKey, true);
    Invoke(digestSocket.Server, digestSession, QueryAccount(accountValue));
    std::vector<CapturedPacket> digestPackets = Drain(*digestSocket.Server);
    CapturedPacket const* badDigest = FindAuthResponse(digestPackets);
    ASSERT_NE(badDigest, nullptr);
    EXPECT_EQ(badDigest->Payload, "000D");
    EXPECT_TRUE(badDigest->Encrypted);
    EXPECT_FALSE(digestSocket.Server->IsOpen());
    EXPECT_FALSE(HasSession(*digestSocket.Server));
    transcript.push_back("bad-digest=000D:encrypted:closed");

    SocketPair successSocket = MakeSocket();
    auto successSession = MakeSession(accountValue, realmId, databaseKey);
    Invoke(successSocket.Server, successSession, QueryAccount(accountValue));
    EXPECT_TRUE(IsAuthenticated(*successSocket.Server));
    ASSERT_TRUE(HasSession(*successSocket.Server));

    std::vector<CapturedPacket> successPackets;
    for (uint32 attempt = 0; attempt < 5000 && !FindAuthResponse(successPackets); ++attempt)
    {
        sWorldSessionMgr->UpdateSessions(1);
        std::vector<CapturedPacket> current = Drain(*successSocket.Server);
        successPackets.insert(successPackets.end(), current.begin(), current.end());
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }

    CapturedPacket const* success = FindAuthResponse(successPackets);
    ASSERT_NE(success, nullptr);
    EXPECT_EQ(success->Payload, "400000000003000000000300000000000C");
    EXPECT_TRUE(success->Encrypted);
    ASSERT_NE(sWorldSessionMgr->FindSession(accountId), nullptr);
    transcript.push_back("success=400000000003000000000300000000000C:encrypted:open");

    std::cout << "PLAN6_WORLD_TRANSCRIPT ";
    for (std::size_t i = 0; i < transcript.size(); ++i)
        std::cout << (i ? "," : "") << transcript[i];
    std::cout << std::endl;

    successSocket.Server->CloseSocket();
    for (uint32 attempt = 0; attempt < 100 && sWorldSessionMgr->FindSession(accountId); ++attempt)
        sWorldSessionMgr->UpdateSessions(1);
    EXPECT_EQ(sWorldSessionMgr->FindSession(accountId), nullptr);

}
