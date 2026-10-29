/*
 * This file is part of the AzerothCore Project. See AUTHORS file for Copyright information
 *
 * This program is free software; you can redistribute it and/or modify it
 * under the terms of the GNU Affero General Public License as published by the
 * Free Software Foundation; either version 3 of the License, or (at your
 * option) any later version.
 *
 * This program is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 * FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License
 * for more details.
 *
 * You should have received a copy of the GNU Affero General Public License
 * along with this program. If not, see <http://www.gnu.org/licenses/>.
 */

#include "CreatureData.h"
#include "gtest/gtest.h"

TEST(CreatureBaseStatsTest, CataclysmExpansionSelectsTheFourthColumn)
{
    EXPECT_EQ(MAX_CREATURE_EXPANSIONS, 4);
    EXPECT_EQ(CreatureStatExpansion(0), 0);
    EXPECT_EQ(CreatureStatExpansion(1), 1);
    EXPECT_EQ(CreatureStatExpansion(2), 2);
    EXPECT_EQ(CreatureStatExpansion(3), 3);
}

TEST(CreatureBaseStatsTest, OutOfRangeExpansionClampsToTheLastColumn)
{
    EXPECT_EQ(CreatureStatExpansion(4), MAX_CREATURE_EXPANSIONS - 1);
    EXPECT_EQ(CreatureStatExpansion(255), MAX_CREATURE_EXPANSIONS - 1);
}

TEST(CreatureBaseStatsTest, StatTablesCarryTheCataclysmColumn)
{
    CreatureBaseStats stats{};
    stats.BaseHealth[2] = 12600;
    stats.BaseHealth[3] = 30951;
    stats.BaseDamage[2] = 164.924f;
    stats.BaseDamage[3] = 197.377f;

    EXPECT_EQ(stats.BaseHealth[CreatureStatExpansion(3)], 30951u);
    EXPECT_FLOAT_EQ(stats.BaseDamage[CreatureStatExpansion(3)], 197.377f);
    EXPECT_EQ(stats.BaseHealth[CreatureStatExpansion(2)], 12600u);
    EXPECT_FLOAT_EQ(stats.BaseDamage[CreatureStatExpansion(2)], 164.924f);
}
