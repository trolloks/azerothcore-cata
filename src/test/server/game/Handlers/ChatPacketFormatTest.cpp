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

#include "Opcodes.h"
#include "SharedDefines.h"
#include "WorldPacket.h"
#include <gtest/gtest.h>

// Locks the Cata 4.3.4 CMSG_MESSAGECHAT_* body layouts (issue #169). The handler read order is
// mirrored here: emote, AFK and DND send no language word, so a language read would consume the
// leading bits of the length-prefixed message.
namespace
{
void WriteLengthPrefixedString(WorldPacket& packet, std::string const& text)
{
    packet.WriteBits(text.size(), 9);
    packet.FlushBits();
    packet.WriteString(text);
}
}

TEST(ChatPacketFormatTest, EmoteAfkDndCarryNoLanguageField)
{
    for (uint16 opcode : { uint16(CMSG_MESSAGECHAT_EMOTE), uint16(CMSG_MESSAGECHAT_AFK), uint16(CMSG_MESSAGECHAT_DND) })
    {
        std::string const message = "hello";
        WorldPacket packet(opcode);
        WriteLengthPrefixedString(packet, message);

        uint32 textLength = packet.ReadBits(9);
        EXPECT_EQ(textLength, message.size());
        EXPECT_EQ(packet.ReadString(textLength), message);
        EXPECT_EQ(packet.rpos(), packet.size());
    }
}

TEST(ChatPacketFormatTest, SayCarriesLanguageBeforeMessage)
{
    std::string const message = "hello";
    WorldPacket packet(uint16(CMSG_MESSAGECHAT_SAY));
    packet << uint32(LANG_COMMON);
    WriteLengthPrefixedString(packet, message);

    uint32 lang;
    packet >> lang;
    uint32 textLength = packet.ReadBits(9);

    EXPECT_EQ(lang, uint32(LANG_COMMON));
    EXPECT_EQ(textLength, message.size());
    EXPECT_EQ(packet.ReadString(textLength), message);
    EXPECT_EQ(packet.rpos(), packet.size());
}

TEST(ChatPacketFormatTest, WhisperCarriesReceiverLengthBeforeMessageLength)
{
    std::string const receiver = "Tester";
    std::string const message = "hello";
    WorldPacket packet(uint16(CMSG_MESSAGECHAT_WHISPER));
    packet << uint32(LANG_COMMON);
    packet.WriteBits(receiver.size(), 10);
    packet.WriteBits(message.size(), 9);
    packet.FlushBits();
    packet.WriteString(receiver);
    packet.WriteString(message);

    uint32 lang;
    packet >> lang;
    uint32 receiverLength = packet.ReadBits(10);
    uint32 textLength = packet.ReadBits(9);

    EXPECT_EQ(lang, uint32(LANG_COMMON));
    EXPECT_EQ(packet.ReadString(receiverLength), receiver);
    EXPECT_EQ(packet.ReadString(textLength), message);
    EXPECT_EQ(packet.rpos(), packet.size());
}
