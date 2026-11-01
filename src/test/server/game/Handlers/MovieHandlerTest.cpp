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
#include "ScriptDefines/ServerScript.h"
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
