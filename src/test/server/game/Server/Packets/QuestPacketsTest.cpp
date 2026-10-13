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

#include "QuestPackets.h"
#include "QueryPackets.h"
#include "QuestDef.h"
#include "Field.h"
#include "World.h"
#include "WorldMock.h"
#include "gtest/gtest.h"

TEST(QuestPacketsTest, NPCQueryResponseUsesCataBitCountsAndGameObjectMask)
{
    WorldPackets::Query::QuestNPCQueryResponse response;
    response.Quests[2158] = {295, 42 | 0x80000000u};
    WorldPacket packet = *response.Write();
    EXPECT_EQ(packet.GetOpcode(), SMSG_QUEST_NPC_QUERY_RESPONSE);
    ASSERT_EQ(packet.size(), 18u);
    EXPECT_EQ(packet.ReadBits(23), 1u);
    EXPECT_EQ(packet.ReadBits(24), 2u);
    EXPECT_EQ(packet.read<uint32>(), 2158u);
    EXPECT_EQ(packet.read<uint32>(), 295u);
    EXPECT_EQ(packet.read<uint32>(), 42u | 0x80000000u);
}

TEST(QuestPacketsTest, RewardChoiceRejectsEmptyAndOutOfRangeSlots)
{
    auto originalWorld = std::move(sWorld);
    sWorld = std::make_unique<testing::NiceMock<WorldMock>>();
    Field fields[108];
    Quest quest(fields);
    sWorld = std::move(originalWorld);
    EXPECT_TRUE(quest.IsValidRewardChoice(0));
    EXPECT_FALSE(quest.IsValidRewardChoice(1));
    EXPECT_FALSE(quest.IsValidRewardChoice(QUEST_REWARD_CHOICES_COUNT));

    quest.RewardChoiceItemId[0] = 57397;
    quest.RewardChoiceItemId[1] = 57396;
    quest.RewardChoiceItemId[2] = 57395;
    for (uint32 choice = 0; choice < 3; ++choice)
        EXPECT_TRUE(quest.IsValidRewardChoice(choice));
    for (uint32 choice = 3; choice <= QUEST_REWARD_CHOICES_COUNT; ++choice)
        EXPECT_FALSE(quest.IsValidRewardChoice(choice));
    EXPECT_FALSE(quest.IsValidRewardChoice(uint32(-1)));
}

TEST(QuestPacketsTest, LifecycleOpcodesMatchBuild15595)
{
    EXPECT_EQ(uint32(DIALOG_STATUS_AVAILABLE), 0x100u);
    EXPECT_EQ(uint32(DIALOG_STATUS_REWARD), 0x400u);
    EXPECT_EQ(uint16(CMSG_QUESTGIVER_ACCEPT_QUEST), 0x6B37);
    EXPECT_EQ(uint16(CMSG_QUESTGIVER_CHOOSE_REWARD), 0x2125);
    EXPECT_EQ(uint16(CMSG_QUESTGIVER_COMPLETE_QUEST), 0x0114);
    EXPECT_EQ(uint16(CMSG_QUESTGIVER_HELLO), 0x0D17);
    EXPECT_EQ(uint16(CMSG_QUESTGIVER_QUERY_QUEST), 0x2F14);
    EXPECT_EQ(uint16(CMSG_QUESTGIVER_REQUEST_REWARD), 0x2534);
    EXPECT_EQ(uint16(CMSG_QUESTLOG_REMOVE_QUEST), 0x0D16);
    EXPECT_EQ(uint16(SMSG_QUESTGIVER_QUEST_DETAILS), 0x2425);
    EXPECT_EQ(uint16(SMSG_QUESTGIVER_OFFER_REWARD), 0x2427);
    EXPECT_EQ(uint16(SMSG_QUESTGIVER_QUEST_COMPLETE), 0x55A4);
}

TEST(QuestPacketsTest, CompleteUsesBuild15595FieldOrderAndTailBits)
{
    WorldPackets::Quest::QuestGiverQuestComplete response;
    response.RewardTalents = 1;
    response.RewardSkillUps = 2;
    response.RewardMoney = 3;
    response.Experience = 4;
    response.QuestId = 5;
    response.RewardSkillLine = 6;
    response.LaunchGossip = true;

    WorldPacket const* packet = response.Write();
    uint8 const expected[] = {
        1, 0, 0, 0, 2, 0, 0, 0, 3, 0, 0, 0,
        4, 0, 0, 0, 5, 0, 0, 0, 6, 0, 0, 0, 0x80
    };
    ASSERT_EQ(packet->size(), sizeof(expected));
    for (size_t index = 0; index < sizeof(expected); ++index)
        EXPECT_EQ(packet->contents()[index], expected[index]) << index;
}

TEST(QuestPacketsTest, CompleteFlushesBothFlagsWithoutExtraFields)
{
    WorldPackets::Quest::QuestGiverQuestComplete response;
    response.UseQuestReward = true;
    WorldPacket const* packet = response.Write();
    ASSERT_EQ(packet->size(), 25u);
    EXPECT_EQ(packet->contents()[24], 0x40);
}

TEST(QuestPacketsTest, RewardsKeepEmptyFixedArraysAndCataTailFields)
{
    WorldPackets::Quest::QuestRewards rewards;
    ByteBuffer data;
    rewards.Write(data);
    ASSERT_EQ(data.size(), 268u);
    for (size_t index = 0; index < data.size(); ++index)
        EXPECT_EQ(data.contents()[index], 0u) << index;
}

TEST(QuestPacketsTest, RewardsGroupIdsQuantitiesAndDisplaysSeparately)
{
    WorldPackets::Quest::QuestRewards rewards;
    rewards.ChoiceItemCount = 1;
    rewards.ChoiceItemId[0] = 57397;
    rewards.ChoiceItemQuantity[0] = 2;
    rewards.ChoiceItemDisplay[0] = 3;
    rewards.ItemCount = 1;
    rewards.ItemId[0] = 4;
    rewards.Money = 5;
    rewards.SkillUps = 6;
    ByteBuffer data;
    rewards.Write(data);
    ASSERT_EQ(data.size(), 268u);
    EXPECT_EQ(data.read<uint32>(0), 1u);
    EXPECT_EQ(data.read<uint32>(4), 57397u);
    EXPECT_EQ(data.read<uint32>(28), 2u);
    EXPECT_EQ(data.read<uint32>(52), 3u);
    EXPECT_EQ(data.read<uint32>(76), 1u);
    EXPECT_EQ(data.read<uint32>(80), 4u);
    EXPECT_EQ(data.read<uint32>(128), 5u);
    EXPECT_EQ(data.read<uint32>(264), 6u);
}
