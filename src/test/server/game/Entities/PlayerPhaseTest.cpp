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

#include "IntegrationTestFixture.h"
#include "Player.h"
#include <gtest/gtest.h>

class PlayerPhaseTest : public IntegrationTestFixture
{
};

TEST_F(PlayerPhaseTest, PhaseSetKeepsUnphasedFlagWithDefaultPhase)
{
    TestPlayer* player = CreateTestPlayer();
    ASSERT_NE(player, nullptr);
    EXPECT_TRUE(player->IsUnphased());

    player->AddPhase(DEFAULT_PHASE, false);
    EXPECT_TRUE(player->IsUnphased());

    player->AddPhase(170, false);
    player->AddPhase(170, false);
    EXPECT_EQ(player->GetPhases().size(), 2u);
    EXPECT_TRUE(player->IsUnphased());

    player->RemovePhase(DEFAULT_PHASE, false);
    EXPECT_FALSE(player->IsUnphased());
    EXPECT_EQ(player->GetPhases().size(), 1u);

    player->RemovePhase(170, false);
    player->RemovePhase(170, false);
    EXPECT_TRUE(player->GetPhases().empty());
    EXPECT_TRUE(player->IsUnphased());
}

TEST_F(PlayerPhaseTest, OverlappingGrantsKeepPhaseUntilLastRemoval)
{
    TestPlayer* player = CreateTestPlayer();
    player->AddPhase(170, false);
    player->AddPhase(170, false);
    EXPECT_EQ(player->GetPhases().size(), 1u);

    player->RemovePhase(170, false);
    EXPECT_EQ(player->GetPhases().size(), 1u);

    player->RemovePhase(170, false);
    EXPECT_TRUE(player->GetPhases().empty());
}

TEST_F(PlayerPhaseTest, PlayersSeeEachOtherOnlyWhenUnphasedOrSharingAPhase)
{
    TestPlayer* one = CreateTestPlayer(1);
    TestPlayer* two = CreateTestPlayer(2);

    EXPECT_TRUE(one->IsPhaseVisibleTo(two)); // both unphased

    two->AddPhase(171, false);
    EXPECT_FALSE(one->IsPhaseVisibleTo(two));
    EXPECT_FALSE(two->IsPhaseVisibleTo(one));

    one->AddPhase(171, false);
    EXPECT_TRUE(one->IsPhaseVisibleTo(two));

    one->RemovePhase(171, false);
    one->AddPhase(DEFAULT_PHASE, false); // still unphased, but no shared phase
    EXPECT_FALSE(one->IsPhaseVisibleTo(two));
}

TEST_F(PlayerPhaseTest, PhasedPlayerSeesGameMasterTargets)
{
    TestPlayer* gm = CreateTestPlayer(3, "TestGM", SEC_GAMEMASTER);
    TestPlayer* phased = CreateTestPlayer(4);

    gm->SetGameMaster(true);
    phased->AddPhase(171, false);

    EXPECT_TRUE(gm->IsPhaseVisibleTo(phased)); // can the phased player see the GM?
}

TEST_F(PlayerPhaseTest, SpawnPhaseZeroIsUnphasedNotAlwaysVisible)
{
    TestPlayer* unphased = CreateTestPlayer(5);
    TestPlayer* phased = CreateTestPlayer(6);
    phased->AddPhase(171, false);

    TestCreature* unphasedSpawn = CreateTestCreature(7, 900001, TEST_FACTION_HOSTILE_TO_ALL);
    unphasedSpawn->SetSpawnPhaseId(0);
    EXPECT_TRUE(unphasedSpawn->IsPhaseVisibleTo(unphased));
    EXPECT_FALSE(unphasedSpawn->IsPhaseVisibleTo(phased));

    TestCreature* questSpawn = CreateTestCreature(8, 900002, TEST_FACTION_HOSTILE_TO_ALL);
    questSpawn->SetSpawnPhaseId(171);
    EXPECT_FALSE(questSpawn->IsPhaseVisibleTo(unphased));
    EXPECT_TRUE(questSpawn->IsPhaseVisibleTo(phased));

    // 169 plus a quest phase is still unphased.
    phased->AddPhase(DEFAULT_PHASE, false);
    EXPECT_TRUE(unphasedSpawn->IsPhaseVisibleTo(phased));
    EXPECT_TRUE(questSpawn->IsPhaseVisibleTo(phased));
}

TEST_F(PlayerPhaseTest, CreatureCompatibilityUsesCataPhases)
{
    TestCreature* one = CreateTestCreature(9, 900003, TEST_FACTION_HOSTILE_TO_ALL);
    TestCreature* two = CreateTestCreature(10, 900004, TEST_FACTION_HOSTILE_TO_ALL);

    one->SetSpawnPhaseId(0);
    two->SetSpawnPhaseId(0);
    EXPECT_TRUE(one->InSamePhase(two)); // both unphased

    two->SetSpawnPhaseId(DEFAULT_PHASE);
    EXPECT_TRUE(one->InSamePhase(two)); // unphased + 169

    two->SetSpawnPhaseId(171);
    EXPECT_FALSE(one->InSamePhase(two)); // unphased + quest phase

    one->SetSpawnPhaseId(171);
    EXPECT_TRUE(one->InSamePhase(two)); // shared 171

    one->SetSpawnPhaseId(172);
    EXPECT_FALSE(one->InSamePhase(two));
}

TEST_F(PlayerPhaseTest, LegacyMaskGateStillHidesScriptControlledObjects)
{
    TestPlayer* player = CreateTestPlayer(13);
    TestCreature* hidden = CreateTestCreature(14, 900006, TEST_FACTION_HOSTILE_TO_ALL);

    hidden->SetPhaseMask(2, false); // script hiding, e.g. an instance portal
    EXPECT_FALSE(hidden->IsPhaseVisibleTo(player));

    hidden->SetPhaseMask(PHASEMASK_NORMAL, false);
    EXPECT_TRUE(hidden->IsPhaseVisibleTo(player));
}
