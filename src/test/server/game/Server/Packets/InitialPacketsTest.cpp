/*
 * This file is part of the AzerothCore Project. See AUTHORS file for Copyright information
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by the
 * Free Software Foundation; either version 2 of the License, or (at your
 * option) any later version.
 */

#include "MiscPackets.h"
#include "MovementPackets.h"
#include "QueryPackets.h"
#include "SpellPackets.h"
#include "SystemPackets.h"
#include "Util.h"
#include <gtest/gtest.h>
#include <span>
#include <string>

namespace
{
    std::string PayloadHex(ByteBuffer const* packet)
    {
        return ByteArrayToHexStr(std::span<uint8 const>(packet->contents(), packet->size()));
    }
}

TEST(InitialPacketsTest, WritesEmptyOptionalInitialPackets)
{
    WorldPackets::Misc::SetupCurrency currencies;
    EXPECT_EQ(PayloadHex(currencies.Write()), "000000");

    WorldPackets::Query::HotfixNotifyBlob hotfixes;
    EXPECT_EQ(PayloadHex(hotfixes.Write()), "000000");

    WorldPackets::Misc::WorldServerInfo worldInfo;
    worldInfo.WeeklyReset = 7;
    worldInfo.DifficultyID = 2;
    EXPECT_EQ(PayloadHex(worldInfo.Write()), "00000700000002000000");
}

TEST(InitialPacketsTest, WritesFeatureStatusAndBindPoint)
{
    WorldPackets::System::FeatureSystemStatus features;
    features.ComplaintStatus = 2;
    features.ScrollOfResurrectionRequestsRemaining = 1;
    features.ScrollOfResurrectionMaxRequestsPerDay = 2;
    features.CfgRealmID = 3;
    features.CfgRealmRecID = 4;
    EXPECT_EQ(PayloadHex(features.Write()), "020100000002000000030000000400000000");

    WorldPackets::Misc::BindPointUpdate bindPoint;
    bindPoint.X = 1.0f;
    bindPoint.Y = 2.0f;
    bindPoint.Z = 3.0f;
    bindPoint.MapID = 4;
    bindPoint.AreaID = 5;
    EXPECT_EQ(PayloadHex(bindPoint.Write()), "0000803F00000040000040400400000005000000");
}

TEST(InitialPacketsTest, WritesSpellsAndActionButtons)
{
    WorldPackets::Spells::SendKnownSpells knownSpells;
    knownSpells.InitialLogin = true;
    knownSpells.KnownSpells = { 0x01020304 };
    knownSpells.SpellHistoryEntries.push_back({ 0x05060708, 9, 10, 11, 12 });
    EXPECT_EQ(PayloadHex(knownSpells.Write()), "010100040302010000010008070605090000000A000B0000000C000000");

    WorldPackets::Spells::SendUnlearnSpells unlearnSpells;
    unlearnSpells.Spells = { 1, 2 };
    EXPECT_EQ(PayloadHex(unlearnSpells.Write()), "020000000100000002000000");

    WorldPackets::Spells::UpdateActionButtons buttons;
    buttons.ActionButtons[0] = 1;
    buttons.ActionButtons[143] = 2;
    buttons.Reason = 0;
    WorldPacket const* buttonsPayload = buttons.Write();
    EXPECT_EQ(buttonsPayload->size(), 577u);
    EXPECT_EQ(buttonsPayload->contents()[0], 1u);
    EXPECT_EQ(buttonsPayload->contents()[572], 2u);
    EXPECT_EQ(buttonsPayload->contents()[576], 0u);
}

TEST(InitialPacketsTest, WritesRuneModifierAndMoverPackets)
{
    WorldPackets::Spells::ResyncRunes runes;
    runes.Runes = { { 1, 2 }, { 3, 4 } };
    EXPECT_EQ(PayloadHex(runes.Write()), "0200000001020304");

    WorldPackets::Spells::SetSpellModifier modifiers(SMSG_SET_FLAT_SPELL_MODIFIER);
    modifiers.Modifiers.push_back({ 7, { { 1.5f, 8 } } });
    EXPECT_EQ(PayloadHex(modifiers.Write()), "010000000100000007080000C03F");

    WorldPackets::Movement::MoveSetActiveMover mover(ObjectGuid::Empty);
    EXPECT_EQ(PayloadHex(mover.Write()), "00");
}
