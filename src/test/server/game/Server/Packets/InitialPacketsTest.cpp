/*
 * This file is part of the AzerothCore Project. See AUTHORS file for Copyright information
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by the
 * Free Software Foundation; either version 2 of the License, or (at your
 * option) any later version.
 */

#include "CharacterPackets.h"
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

TEST(InitialPacketsTest, WritesEmptyAndNonEmptyPhaseShiftChange)
{
    // All-nonzero guid bytes so WriteByteSeq emits every Client[i] byte (it silently skips zero
    // bytes), keeping the fixed-field layout below a constant number of bytes:
    // 1 (bits) + 1 (Client[7]) + 1 (Client[4]) + 4 (UiMapPhaseIDs size) + 1 (Client[1])
    // + 4 (PhaseShiftFlags) + 1 (Client[2]) + 1 (Client[6]) + 4 (PreloadMapIDs size) = 18 bytes
    // before the Phases size prefix; PhaseShiftFlags itself sits at offset 8.
    ObjectGuid const guid(uint64(0x0807060504030201));

    WorldPackets::Misc::PhaseShiftChange empty;
    empty.Client = guid;
    WorldPacket const* emptyPayload = empty.Write();
    // 18 (fixed fields above) + 4 (Phases size, 0 entries) + 1 (Client[3]) + 1 (Client[0])
    // + 4 (VisibleMapIDs size) + 1 (Client[5]) = 29 bytes.
    EXPECT_EQ(emptyPayload->size(), 29u);
    EXPECT_EQ(uint32(emptyPayload->contents()[8]) | uint32(emptyPayload->contents()[9]) << 8 |
                  uint32(emptyPayload->contents()[10]) << 16 | uint32(emptyPayload->contents()[11]) << 24,
              0x8u); // default PhaseShiftFlags::Unphased
    EXPECT_EQ(uint32(emptyPayload->contents()[18]) | uint32(emptyPayload->contents()[19]) << 8 |
                  uint32(emptyPayload->contents()[20]) << 16 | uint32(emptyPayload->contents()[21]) << 24,
              0u); // no phases

    WorldPackets::Misc::PhaseShiftChange withPhases;
    withPhases.Client = guid;
    withPhases.PhaseShiftFlags = 0;
    withPhases.Phases = { 125, 171 };
    WorldPacket const* payload = withPhases.Write();
    EXPECT_EQ(payload->size(), emptyPayload->size() + 4u); // 2 extra uint16 phase entries
    EXPECT_EQ(uint32(payload->contents()[18]) | uint32(payload->contents()[19]) << 8, 4u); // 2 phases * 2 bytes
    EXPECT_EQ(uint16(payload->contents()[22]) | uint16(payload->contents()[23]) << 8, 125u);
    EXPECT_EQ(uint16(payload->contents()[24]) | uint16(payload->contents()[25]) << 8, 171u);
}

TEST(InitialPacketsTest, WritesLoginVerifyWorld)
{
    WorldPackets::Character::LoginVerifyWorld loginVerifyWorld;
    loginVerifyWorld.MapID = 4;
    loginVerifyWorld.Pos = Position(1.0f, 2.0f, 3.0f, 4.0f);
    WorldPacket const* payload = loginVerifyWorld.Write();
    EXPECT_EQ(payload->size(), 20u);
    EXPECT_EQ(PayloadHex(payload), "040000000000803F000000400000404000008040");
}
