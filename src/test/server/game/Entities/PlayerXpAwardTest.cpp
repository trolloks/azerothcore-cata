/*
 * This file is part of the AzerothCore Project. See AUTHORS file for Copyright information
 *
 * This program is free software; you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the
 * Free Software Foundation; either version 2 of the License, or (at your
 * option) any later version.
 */

#include "IntegrationTestFixture.h"
#include "Player.h"
#include "WorldConfig.h"
#include <gtest/gtest.h>

// Plan 31 / issue #102 deterministic coverage for Player::GiveXP. The level-1 -> 2 threshold of 400
// is the frozen plan22 Human Warrior input (player_xp_for_level row (1, 400)).
namespace
{
    constexpr uint32 LEVEL_ONE_XP_TO_LEVEL_TWO = 400;
    constexpr uint32 TEST_LEVEL_CAP             = 80;
}

class PlayerXpAwardTest : public IntegrationTestFixture
{
protected:
    void SetUp() override
    {
        IntegrationTestFixture::SetUp();
        // WorldMock answers every int config with 0, which would make GiveXP treat even level 1 as
        // already at the cap. A specific ON_CALL overrides the fixture's catch-all.
        ON_CALL(*GetWorldMock(), getIntConfig(CONFIG_MAX_PLAYER_LEVEL)).WillByDefault(Return(TEST_LEVEL_CAP));
    }

    TestPlayer* CreateLevelOnePlayer()
    {
        TestPlayer* player = CreateTestPlayer();
        player->SetLevel(1);
        player->SetUInt32Value(PLAYER_XP, 0);
        player->SetUInt32Value(PLAYER_NEXT_LEVEL_XP, LEVEL_ONE_XP_TO_LEVEL_TWO);
        return player;
    }
};

// An ineligible XP source must never move the player's XP, in every early-out shape GiveXP has.
TEST_F(PlayerXpAwardTest, IgnoresIneligibleXpSources)
{
    TestPlayer* player = CreateLevelOnePlayer();

    // Zero XP.
    player->GiveXP(0, nullptr, 1.0f, false);
    EXPECT_EQ(player->GetUInt32Value(PLAYER_XP), 0u);

    // Player flagged as not gaining XP.
    player->SetFlag(PLAYER_FLAGS, PLAYER_FLAGS_NO_XP_GAIN);
    player->GiveXP(100, nullptr, 1.0f, false);
    EXPECT_EQ(player->GetUInt32Value(PLAYER_XP), 0u);
    player->RemoveFlag(PLAYER_FLAGS, PLAYER_FLAGS_NO_XP_GAIN);

    // Dead player, outside a battleground and not an LFG reward.
    player->SetDeathStateForTest(DeathState::Dead);
    player->GiveXP(100, nullptr, 1.0f, false);
    EXPECT_EQ(player->GetUInt32Value(PLAYER_XP), 0u);
    player->SetDeathStateForTest(DeathState::Alive);

    // Creature kill that nobody looted: without a loot recipient the kill is not the player's to
    // claim XP from.
    TestCreature* creature = CreateTestCreature(10, 1, TEST_FACTION_HOSTILE_TO_ALL);
    player->GiveXP(100, creature, 1.0f, false);
    EXPECT_EQ(player->GetUInt32Value(PLAYER_XP), 0u);

    // Already at the level cap.
    player->SetLevel(TEST_LEVEL_CAP);
    player->GiveXP(100, nullptr, 1.0f, false);
    EXPECT_EQ(player->GetUInt32Value(PLAYER_XP), 0u);
    EXPECT_EQ(player->GetLevel(), TEST_LEVEL_CAP);
}

// Below the threshold, XP accumulates and the level/threshold fields are untouched.
TEST_F(PlayerXpAwardTest, AccumulatesXpBelowThreshold)
{
    TestPlayer* player = CreateLevelOnePlayer();

    player->GiveXP(250, nullptr, 1.0f, false);

    EXPECT_EQ(player->GetUInt32Value(PLAYER_XP), 250u);
    EXPECT_EQ(player->GetLevel(), 1);
    EXPECT_EQ(player->GetUInt32Value(PLAYER_NEXT_LEVEL_XP), LEVEL_ONE_XP_TO_LEVEL_TWO);
}

// One XP short of the threshold must not level up.
TEST_F(PlayerXpAwardTest, OneShortOfThresholdDoesNotLevelUp)
{
    TestPlayer* player = CreateLevelOnePlayer();

    player->GiveXP(LEVEL_ONE_XP_TO_LEVEL_TWO - 1, nullptr, 1.0f, false);

    EXPECT_EQ(player->GetUInt32Value(PLAYER_XP), LEVEL_ONE_XP_TO_LEVEL_TWO - 1);
    EXPECT_EQ(player->GetLevel(), 1);
}

// The crossing case (award exactly the threshold -> level 2, no remainder) is deliberately not
// asserted here: GiveLevel reaches world-DB and singleton state the unit harness does not provide
// and the test process aborts. That case is covered by the real-client acceptance run, which
// earns the threshold through a normal quest/kill award.