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
#include "gtest/gtest.h"

// A spawn with no Cata phase assigned (the default) is always visible, regardless of the
// viewer's phase membership.
TEST_F(IntegrationTestFixture, UngatedSpawnIsAlwaysVisible)
{
    TestPlayer* player = CreateTestPlayer(1, "PhaseTest", SEC_PLAYER);
    player->SetPhasesForTest({});

    TestCreature* creature = CreateTestCreature(2, 1, TEST_FACTION_HOSTILE_TO_MONSTERS);
    EXPECT_EQ(creature->GetSpawnPhaseId(), 0u);
    EXPECT_TRUE(creature->IsPhaseVisibleTo(player));
}

// A spawn gated to a phase the player is in is visible.
TEST_F(IntegrationTestFixture, GatedSpawnVisibleWhenPlayerSharesPhase)
{
    TestPlayer* player = CreateTestPlayer(1, "PhaseTest", SEC_PLAYER);
    player->SetPhasesForTest({125});

    TestCreature* creature = CreateTestCreature(2, 1, TEST_FACTION_HOSTILE_TO_MONSTERS);
    creature->SetSpawnPhaseId(125);
    EXPECT_TRUE(creature->IsPhaseVisibleTo(player));
}

// A spawn gated to a phase the player is not in is hidden.
TEST_F(IntegrationTestFixture, GatedSpawnHiddenWhenPlayerLacksPhase)
{
    TestPlayer* player = CreateTestPlayer(1, "PhaseTest", SEC_PLAYER);
    player->SetPhasesForTest({171});

    TestCreature* creature = CreateTestCreature(2, 1, TEST_FACTION_HOSTILE_TO_MONSTERS);
    creature->SetSpawnPhaseId(125);
    EXPECT_FALSE(creature->IsPhaseVisibleTo(player));

    // CanNeverSee must agree for player viewers: a phase-hidden spawn can never be seen.
    EXPECT_FALSE(player->CanSeeOrDetect(creature));
}
