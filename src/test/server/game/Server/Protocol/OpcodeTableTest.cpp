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

#include "GuildPackets.h"
#include "Opcodes.h"
#include "gtest/gtest.h"

TEST(OpcodeTableTest, KeepsDirectionsIndependent)
{
    OpcodeTable table;
    table.Initialize();

    OpcodeClient clientOverlap = static_cast<OpcodeClient>(0x0014);
    OpcodeServer serverOverlap = static_cast<OpcodeServer>(0x0014);
    ASSERT_NE(table[clientOverlap], nullptr);
    ASSERT_NE(table[serverOverlap], nullptr);
    EXPECT_STREQ(table[clientOverlap]->Name, "CMSG_CREATEGAMEOBJECT");
    EXPECT_STREQ(table[serverOverlap]->Name, "SMSG_GAMETIME_SET");
    EXPECT_EQ(table.GetOpcodeNameForLogging(clientOverlap), "[CMSG_CREATEGAMEOBJECT 0x0014 (20)]");
    EXPECT_EQ(table.GetOpcodeNameForLogging(serverOverlap), "[SMSG_GAMETIME_SET 0x0014 (20)]");

    ASSERT_NE(table[CMSG_GMTICKET_CREATE], nullptr);
    ASSERT_NE(table[CMSG_ACCEPT_LEVEL_GRANT], nullptr);
    EXPECT_NE(table[CMSG_GMTICKET_CREATE], table[CMSG_ACCEPT_LEVEL_GRANT]);
    EXPECT_STREQ(table[CMSG_GMTICKET_CREATE]->Name, "CMSG_GMTICKET_CREATE");
    EXPECT_STREQ(table[CMSG_ACCEPT_LEVEL_GRANT]->Name, "CMSG_ACCEPT_LEVEL_GRANT");

    EXPECT_EQ(table.GetOpcodeNameForLogging(MSG_MINIMAP_PING_SERVER), "[MSG_MINIMAP_PING 0x6635 (26165)]");

    uint16 serverOnly = uint16(SMSG_AUTH_RESPONSE);
    EXPECT_NE(table.GetIncomingOpcode(serverOnly), nullptr);
    EXPECT_EQ(table[static_cast<OpcodeClient>(serverOnly)], nullptr);

    EXPECT_EQ(table.GetOpcodeNameForLogging(static_cast<OpcodeClient>(NUM_OPCODE_HANDLERS)),
        "[INVALID OPCODE 0xFFFF (65535)]");
}

TEST(OpcodeTableTest, AcceptsCataclysmGuildQueriesSentAfterWorldEntry)
{
    OpcodeTable table;
    table.Initialize();

    OpcodeHandler const* moneyQuery = table.GetIncomingOpcode(0x1225);
    ASSERT_NE(moneyQuery, nullptr);
    EXPECT_STREQ(moneyQuery->Name, "CMSG_GUILD_BANK_REMAINING_WITHDRAW_MONEY_QUERY");
    EXPECT_EQ(moneyQuery->Status, STATUS_LOGGEDIN);
    EXPECT_NE(table[static_cast<OpcodeClient>(0x1225)], nullptr);

    OpcodeHandler const* tracking = table.GetIncomingOpcode(0x1027);
    ASSERT_NE(tracking, nullptr);
    EXPECT_STREQ(tracking->Name, "CMSG_GUILD_SET_ACHIEVEMENT_TRACKING");
    EXPECT_EQ(tracking->Status, STATUS_UNHANDLED);

    WorldPackets::Guild::GuildBankRemainingWithdrawMoney response;
    response.RemainingWithdrawMoney = -1;
    WorldPacket const* packet = response.Write();
    EXPECT_EQ(packet->GetOpcode(), 0x5DB4);
    ASSERT_EQ(packet->size(), 8u);
    for (std::size_t index = 0; index < packet->size(); ++index)
        EXPECT_EQ(packet->contents()[index], 0xFF);
}

// Locks in the Cata 4.3.4 melee auto-attack opcode values (verified against the cata-js test
// client). These previously carried over the WotLK 3.3.5 values unchanged, so the real 4.3.4
// client's CMSG_ATTACKSWING packet (sent as 0x0926) was rejected as an unknown opcode and the
// session dropped as soon as the player attacked.
TEST(OpcodeTableTest, UsesCataclysmMeleeAttackOpcodeValues)
{
    EXPECT_EQ(uint16(CMSG_ATTACKSWING), 0x0926);
    EXPECT_EQ(uint16(CMSG_ATTACKSTOP), 0x4106);
    EXPECT_EQ(uint16(SMSG_ATTACKSTART), 0x2D15);
    EXPECT_EQ(uint16(SMSG_ATTACKSTOP), 0x0934);
    EXPECT_EQ(uint16(SMSG_ATTACKERSTATEUPDATE), 0x0B25);

    OpcodeTable table;
    table.Initialize();

    OpcodeHandler const* attackSwing = table.GetIncomingOpcode(0x0926);
    ASSERT_NE(attackSwing, nullptr);
    EXPECT_STREQ(attackSwing->Name, "CMSG_ATTACKSWING");
    EXPECT_EQ(attackSwing->Status, STATUS_LOGGEDIN);
}

// Locks in the Cata 4.3.4 per-chat-type opcode split (verified against the pinned
// TrinityCore-Cata reference). WotLK's single CMSG_MESSAGECHAT = 0x095 carried the chat type as
// the first packet field; the real 4.3.4 client instead sends one distinct opcode per chat type,
// so a real client's ordinary /say (CMSG_MESSAGECHAT_SAY, 0x1154) was rejected as an unknown
// opcode and the connection dropped as soon as the player sent a chat message (issue #89).
TEST(OpcodeTableTest, UsesCataclysmChatOpcodeValues)
{
    EXPECT_EQ(uint16(CMSG_MESSAGECHAT_SAY), 0x1154);
    EXPECT_EQ(uint16(CMSG_MESSAGECHAT_YELL), 0x3544);
    EXPECT_EQ(uint16(CMSG_MESSAGECHAT_CHANNEL), 0x1D44);
    EXPECT_EQ(uint16(CMSG_MESSAGECHAT_WHISPER), 0x0D56);
    EXPECT_EQ(uint16(CMSG_MESSAGECHAT_GUILD), 0x3956);
    EXPECT_EQ(uint16(CMSG_MESSAGECHAT_OFFICER), 0x1946);
    EXPECT_EQ(uint16(CMSG_MESSAGECHAT_AFK), 0x0D44);
    EXPECT_EQ(uint16(CMSG_MESSAGECHAT_DND), 0x2946);
    EXPECT_EQ(uint16(CMSG_MESSAGECHAT_EMOTE), 0x1156);
    EXPECT_EQ(uint16(CMSG_MESSAGECHAT_PARTY), 0x1D46);
    EXPECT_EQ(uint16(CMSG_MESSAGECHAT_RAID), 0x2D44);
    EXPECT_EQ(uint16(CMSG_MESSAGECHAT_BATTLEGROUND), 0x2156);
    EXPECT_EQ(uint16(CMSG_MESSAGECHAT_RAID_WARNING), 0x0944);
    EXPECT_EQ(uint16(CMSG_MESSAGECHAT_ADDON_BATTLEGROUND), 0x0D46);
    EXPECT_EQ(uint16(CMSG_MESSAGECHAT_ADDON_GUILD), 0x0544);
    EXPECT_EQ(uint16(CMSG_MESSAGECHAT_ADDON_OFFICER), 0x3954);
    EXPECT_EQ(uint16(CMSG_MESSAGECHAT_ADDON_PARTY), 0x0546);
    EXPECT_EQ(uint16(CMSG_MESSAGECHAT_ADDON_RAID), 0x1D56);
    EXPECT_EQ(uint16(CMSG_MESSAGECHAT_ADDON_WHISPER), 0x2146);

    OpcodeTable table;
    table.Initialize();

    OpcodeHandler const* say = table.GetIncomingOpcode(0x1154);
    ASSERT_NE(say, nullptr);
    EXPECT_STREQ(say->Name, "CMSG_MESSAGECHAT_SAY");
    EXPECT_EQ(say->Status, STATUS_LOGGEDIN);

    OpcodeHandler const* addonGuild = table.GetIncomingOpcode(0x0544);
    ASSERT_NE(addonGuild, nullptr);
    EXPECT_STREQ(addonGuild->Name, "CMSG_MESSAGECHAT_ADDON_GUILD");
    EXPECT_EQ(addonGuild->Status, STATUS_LOGGEDIN);
}
