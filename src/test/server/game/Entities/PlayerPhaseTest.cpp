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

TEST_F(PlayerPhaseTest, HasPhaseIsExactMembership)
{
    TestPlayer* player = CreateTestPlayer(15);
    EXPECT_FALSE(player->HasPhase(DEFAULT_PHASE)); // unphased is not membership
    player->AddPhase(170, false);
    EXPECT_TRUE(player->HasPhase(170));
    EXPECT_FALSE(player->HasPhase(DEFAULT_PHASE));

    TestCreature* spawn = CreateTestCreature(16, 900007, TEST_FACTION_HOSTILE_TO_ALL);
    spawn->SetSpawnPhaseId(DEFAULT_PHASE);
    EXPECT_TRUE(spawn->HasPhase(DEFAULT_PHASE));
    EXPECT_FALSE(spawn->HasPhase(170));

    TestCreature* unphased = CreateTestCreature(17, 900008, TEST_FACTION_HOSTILE_TO_ALL);
    unphased->SetSpawnPhaseId(0);
    EXPECT_FALSE(unphased->HasPhase(DEFAULT_PHASE));
}

TEST_F(PlayerPhaseTest, CreatureAuraPhasesAffectVisibility)
{
    TestPlayer* unphased = CreateTestPlayer(18);
    TestPlayer* auraPhase = CreateTestPlayer(19);
    auraPhase->AddPhase(170, false);
    TestPlayer* spawnPhase = CreateTestPlayer(28);
    spawnPhase->AddPhase(171, false);

    TestCreature* creature = CreateTestCreature(20, 900009, TEST_FACTION_HOSTILE_TO_ALL);
    creature->SetSpawnPhaseId(171);
    creature->AddPhase(170, false);

    EXPECT_FALSE(creature->IsPhaseVisibleTo(unphased));
    EXPECT_TRUE(creature->IsPhaseVisibleTo(auraPhase));
    EXPECT_TRUE(creature->IsPhaseVisibleTo(spawnPhase));
    EXPECT_TRUE(creature->HasPhase(170));

    creature->RemovePhase(170, false);
    EXPECT_FALSE(creature->IsPhaseVisibleTo(unphased));
    EXPECT_FALSE(creature->IsPhaseVisibleTo(auraPhase));
    EXPECT_TRUE(creature->IsPhaseVisibleTo(spawnPhase));
}

TEST_F(PlayerPhaseTest, SummonsInheritOwnerPhases)
{
    TestPlayer* owner = CreateTestPlayer(21);
    owner->AddPhase(171, false);
    TestPlayer* same = CreateTestPlayer(22);
    same->AddPhase(171, false);
    TestPlayer* other = CreateTestPlayer(23);
    other->AddPhase(172, false);

    TestCreature* summon = CreateTestCreature(24, 900010, TEST_FACTION_HOSTILE_TO_ALL);
    summon->InheritPhasesFrom(owner);

    EXPECT_TRUE(summon->IsPhaseVisibleTo(same));
    EXPECT_FALSE(summon->IsPhaseVisibleTo(other));
    EXPECT_TRUE(summon->HasPhase(171));

    TestPlayer* unphasedOwner = CreateTestPlayer(25);
    TestCreature* unphasedSummon = CreateTestCreature(26, 900011, TEST_FACTION_HOSTILE_TO_ALL);
    unphasedSummon->InheritPhasesFrom(unphasedOwner);
    TestPlayer* unphased = CreateTestPlayer(27);
    EXPECT_TRUE(unphasedSummon->IsPhaseVisibleTo(unphased));
    EXPECT_FALSE(unphasedSummon->IsPhaseVisibleTo(same));
}

TEST_F(PlayerPhaseTest, InheritanceKeepsOwnPhaseGrants)
{
    TestPlayer* owner = CreateTestPlayer(29);
    owner->AddPhase(171, false);
    TestPlayer* ownPhase = CreateTestPlayer(30);
    ownPhase->AddPhase(170, false);
    TestPlayer* inheritedPhase = CreateTestPlayer(31);
    inheritedPhase->AddPhase(171, false);

    TestCreature* summon = CreateTestCreature(32, 900012, TEST_FACTION_HOSTILE_TO_ALL);
    summon->AddPhase(170, false); // own aura grant
    summon->InheritPhasesFrom(owner);

    EXPECT_TRUE(summon->IsPhaseVisibleTo(ownPhase));
    EXPECT_TRUE(summon->IsPhaseVisibleTo(inheritedPhase));
    EXPECT_TRUE(summon->HasPhase(170));
    EXPECT_TRUE(summon->HasPhase(171));

    summon->RemovePhase(170, false);
    EXPECT_FALSE(summon->IsPhaseVisibleTo(ownPhase));
    EXPECT_TRUE(summon->IsPhaseVisibleTo(inheritedPhase));
}

TEST_F(PlayerPhaseTest, UnphasedOwnerSummonKeepsOwnGrantsVisible)
{
    TestPlayer* owner = CreateTestPlayer(33);
    TestPlayer* ownPhase = CreateTestPlayer(35);
    ownPhase->AddPhase(170, false);
    TestPlayer* otherPhase = CreateTestPlayer(36);
    otherPhase->AddPhase(172, false);

    TestCreature* summon = CreateTestCreature(34, 900013, TEST_FACTION_HOSTILE_TO_ALL);
    summon->AddPhase(170, false);
    summon->InheritPhasesFrom(owner);

    EXPECT_TRUE(summon->IsPhaseVisibleTo(owner));
    EXPECT_TRUE(summon->IsPhaseVisibleTo(ownPhase));
    EXPECT_FALSE(summon->IsPhaseVisibleTo(otherPhase));
    EXPECT_FALSE(summon->HasPhase(DEFAULT_PHASE)); // unphased visibility is not a grant
}

TEST_F(PlayerPhaseTest, UnphasedCreatureOwnerDoesNotGrantPhaseMembership)
{
    TestCreature* owner = CreateTestCreature(37, 900014, TEST_FACTION_HOSTILE_TO_ALL);
    owner->SetSpawnPhaseId(0);

    TestCreature* summon = CreateTestCreature(38, 900015, TEST_FACTION_HOSTILE_TO_ALL);
    summon->InheritPhasesFrom(owner);

    EXPECT_FALSE(summon->HasPhase(DEFAULT_PHASE));
    TestPlayer* unphased = CreateTestPlayer(39);
    EXPECT_TRUE(summon->IsPhaseVisibleTo(unphased)); // unphased visibility, not a grant
}
