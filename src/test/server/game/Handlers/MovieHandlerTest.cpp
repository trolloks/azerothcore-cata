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

#include "IntegrationTestFixture.h"
#include "Player.h"
#include "DBCStores.h"
#include "ScriptDefines/ServerScript.h"
#include "Spell.h"
#include "SpellInfoTestHelper.h"
#include "WorldPacket.h"
#include <gtest/gtest.h>

class MovieHandlerTest : public IntegrationTestFixture
{
protected:
    void SetUp() override
    {
        IntegrationTestFixture::SetUp();
        ScriptRegistry<ServerScript>::InitEnabledHooksIfNeeded(SERVERHOOK_END);
    }
};

TEST_F(MovieHandlerTest, StartsAndClearsPendingMovie)
{
    TestPlayer* player = CreateTestPlayer();
    ASSERT_NE(player, nullptr);
    EXPECT_EQ(player->GetMovie(), 0u);

    player->SendMovieStart(42);
    EXPECT_EQ(player->GetMovie(), 42u);

    WorldPacket complete(CMSG_COMPLETE_MOVIE);
    player->GetSession()->HandleCompleteMovie(complete);
    EXPECT_EQ(player->GetMovie(), 0u);

    // A repeat acknowledgement is harmless when no movie is pending.
    player->GetSession()->HandleCompleteMovie(complete);
    EXPECT_EQ(player->GetMovie(), 0u);
}

TEST_F(MovieHandlerTest, MovieSpellRequiresAClientMovieEntry)
{
    TestPlayer* player = CreateTestPlayer();
    constexpr uint32 testMovieId = 900001;
    auto spellInfo = SpellInfoBuilder()
        .WithEffect(EFFECT_0, SPELL_EFFECT_PLAY_MOVIE)
        .WithEffectMiscValue(EFFECT_0, testMovieId)
        .BuildUnique();
    Spell spell(player, spellInfo.get(), TRIGGERED_FULL_MASK);
    spell.unitTarget = player;
    spell.effectHandleMode = SPELL_EFFECT_HANDLE_HIT_TARGET;

    spell.EffectPlayMovie(EFFECT_0);
    EXPECT_EQ(player->GetMovie(), 0u);

    sMovieStore.SetEntry(testMovieId, new MovieEntry{ testMovieId });
    spell.EffectPlayMovie(EFFECT_0);
    EXPECT_EQ(player->GetMovie(), testMovieId);
    sMovieStore.SetEntry(testMovieId, nullptr);
}

TEST(MovieSpellTargets, CampaignEffectsRequireAUnitTarget)
{
    for (SpellEffects effect : { SPELL_EFFECT_PLAY_MOVIE, SPELL_EFFECT_UPDATE_PLAYER_PHASE,
        SPELL_EFFECT_UPDATE_ZONE_AURAS_AND_PHASES })
    {
        auto spellInfo = SpellInfoBuilder().WithEffect(EFFECT_0, effect).BuildUnique();
        EXPECT_EQ(spellInfo->Effects[EFFECT_0].GetUsedTargetObjectType(), TARGET_OBJECT_TYPE_UNIT);
        EXPECT_EQ(spellInfo->Effects[EFFECT_0].GetImplicitTargetType(), EFFECT_IMPLICIT_TARGET_EXPLICIT);
    }
}
