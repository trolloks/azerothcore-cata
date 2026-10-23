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
#include <gtest/gtest.h>

// 4.3.4 writes the primary talent tree (TalentTab.dbc id) before each spec's talent list;
// the 3.3.5a layout omitted it, so the client misread group, talents and glyphs (#171).
class TalentsInfoTest : public IntegrationTestFixture
{
};

TEST_F(TalentsInfoTest, WritesPrimaryTalentTreeBeforeTheTalentCount)
{
    TestPlayer* player = CreateTestPlayer();
    player->SetSpecsCount(1);
    player->SetActiveSpec(0);

    WorldPacket data;
    player->BuildPlayerTalentsInfoData(&data);

    uint32 unspentTalentPoints = 0;
    uint8 specsCount = 0;
    uint8 activeSpec = 0;
    uint32 primaryTalentTree = 1;
    uint8 talentCount = 1;
    uint8 glyphCount = 0;
    data >> unspentTalentPoints >> specsCount >> activeSpec >> primaryTalentTree >> talentCount >> glyphCount;

    EXPECT_EQ(specsCount, 1);
    EXPECT_EQ(activeSpec, 0);
    EXPECT_EQ(primaryTalentTree, 0u);                 // no learned talents yet
    EXPECT_EQ(talentCount, 0u);
    EXPECT_EQ(glyphCount, uint8(MAX_GLYPH_SLOT_INDEX));
    EXPECT_EQ(data.rpos() + MAX_GLYPH_SLOT_INDEX * sizeof(uint16), data.size());
}
