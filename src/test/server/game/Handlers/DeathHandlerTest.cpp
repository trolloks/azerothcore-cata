/*
 * This file is part of the AzerothCore Project. See AUTHORS file for Copyright information
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by the
 * Free Software Foundation; either version 2 of the License, or (at your
 * option) any later version.
 */

#include "Corpse.h"
#include "IntegrationTestFixture.h"
#include "SpellAuras.h"
#include "SpellInfoTestHelper.h"
#include "SpellPackets.h"
#include "WorldPacket.h"

namespace
{
class DeathHandlerTest : public IntegrationTestFixture
{
protected:
    void TearDown() override
    {
        for (Corpse* corpse : _corpses)
        {
            GetTestMap()->RemoveCorpse(corpse);
            delete corpse;
        }
        IntegrationTestFixture::TearDown();
    }

    TestPlayer* CreateGhost(uint32 guid = 1)
    {
        TestPlayer* player = CreateTestPlayer(guid);
        player->SetMaxHealth(100);
        player->SetHealth(1);
        player->SetDeathStateForTest(DeathState::Corpse);
        player->SetPlayerFlag(PLAYER_FLAGS_GHOST);
        return player;
    }

    Corpse* CreateCorpse(TestPlayer* owner)
    {
        auto* corpse = new Corpse(CORPSE_RESURRECTABLE_PVE);
        EXPECT_TRUE(corpse->Create(uint32(_corpses.size()) + 100, owner));
        GetTestMap()->AddCorpse(corpse);
        _corpses.push_back(corpse);
        return corpse;
    }

    std::vector<Corpse*> _corpses;
};
}

TEST_F(DeathHandlerTest, MeleeDamageTakenRoundsUpAfterPercentageReduction)
{
    TestPlayer* player = CreateTestPlayer();
    TestCreature* wolf = CreateTestCreature(1, 299, TEST_FACTION_HOSTILE_TO_ALL);
    auto spell = SpellInfoBuilder()
        .WithId(900007)
        .WithEffect(0, SPELL_EFFECT_APPLY_AURA, SPELL_AURA_MOD_DAMAGE_PERCENT_TAKEN)
        .WithEffectBasePoints(0, -5)
        .WithEffectMiscValue(0, SPELL_SCHOOL_MASK_NORMAL)
        .BuildUnique();
    Aura* aura = Aura::TryCreate(spell.get(), 1, player, player);
    ASSERT_NE(aura, nullptr);
    aura->ApplyForTargets();

    EXPECT_EQ(player->MeleeDamageBonusTaken(wolf, 0, BASE_ATTACK), 0u);
    EXPECT_EQ(player->MeleeDamageBonusTaken(wolf, 1, BASE_ATTACK), 1u);
    EXPECT_EQ(player->MeleeDamageBonusTaken(wolf, 20, BASE_ATTACK), 19u);

    aura->Remove();
}

TEST_F(DeathHandlerTest, WarriorRecoveryCannotWriteInactiveEnergySlot)
{
    TestPlayer* player = CreateGhost();
    player->SetByteValue(UNIT_FIELD_BYTES_0, 1, CLASS_WARRIOR);
    player->SetMaxPower(POWER_RAGE, 1000);
    player->SetPower(POWER_RAGE, 300, false);
    player->SetMaxPower(POWER_ENERGY, 100);
    player->SetPower(POWER_ENERGY, 50, false);

    EXPECT_EQ(player->GetUInt32Value(UNIT_FIELD_POWER1), 300u);
    EXPECT_EQ(player->GetUInt32Value(UNIT_FIELD_MAXPOWER1), 1000u);
    EXPECT_EQ(player->GetUInt32Value(UNIT_FIELD_POWER4), 0u);
    EXPECT_EQ(player->GetMaxPower(POWER_ENERGY), 0u);
    EXPECT_EQ(player->GetPower(POWER_ENERGY), 0u);
    EXPECT_EQ(player->GetPowerIndex(POWER_ALL), uint32(MAX_POWERS));
    EXPECT_EQ(player->GetMaxHealth(), 100u);
}

TEST_F(DeathHandlerTest, PowerSlotsFollowClassDataRatherThanPowerEnum)
{
    struct Case
    {
        uint8 classId;
        Powers power;
        uint32 index;
    };
    uint32 guid = 1;
    for (Case const& test : {
        Case{ CLASS_WARRIOR, POWER_RAGE, 0 }, { CLASS_HUNTER, POWER_FOCUS, 0 },
        { CLASS_ROGUE, POWER_ENERGY, 0 }, { CLASS_DEATH_KNIGHT, POWER_RUNIC_POWER, 0 },
        { CLASS_DRUID, POWER_MANA, 0 }, { CLASS_DRUID, POWER_RAGE, 1 }, { CLASS_DRUID, POWER_ENERGY, 2 } })
    {
        TestPlayer* player = CreateTestPlayer(guid++);
        player->SetByteValue(UNIT_FIELD_BYTES_0, 1, test.classId);
        player->SetMaxHealth(100);
        player->SetMaxPower(test.power, 1000);
        player->SetPower(test.power, 300, false, true);

        EXPECT_EQ(player->GetPowerIndex(test.power), test.index);
        EXPECT_EQ(player->GetUInt32Value(UNIT_FIELD_POWER1 + test.index), 300u);
        EXPECT_EQ(player->GetMaxPower(test.power), 1000u);
        EXPECT_EQ(player->GetMaxHealth(), 100u);
    }
}

TEST_F(DeathHandlerTest, NonPlayerPowersRetainExistingSlots)
{
    TestCreature* creature = CreateTestCreature(1, 299, TEST_FACTION_HOSTILE_TO_ALL);
    creature->SetByteValue(UNIT_FIELD_BYTES_0, 3, POWER_ENERGY);
    creature->SetMaxPower(POWER_ENERGY, 100);
    creature->SetPower(POWER_ENERGY, 50, false);

    creature->SetMaxPower(POWER_HAPPINESS, 200000);
    creature->SetPower(POWER_HAPPINESS, 166500, false);

    EXPECT_EQ(creature->GetUInt32Value(UNIT_FIELD_POWER1), 0u);
    EXPECT_EQ(creature->GetUInt32Value(UNIT_FIELD_POWER4), 50u);
    EXPECT_EQ(creature->GetPower(POWER_ENERGY), 50u);
    EXPECT_EQ(creature->GetPower(POWER_HAPPINESS), 166500u);
}

TEST_F(DeathHandlerTest, PrematureReclaimKeepsOwnedCorpseAndGhostState)
{
    TestPlayer* player = CreateGhost();
    Corpse* corpse = CreateCorpse(player);
    ON_CALL(*GetWorldMock(), getBoolConfig(CONFIG_DEATH_CORPSE_RECLAIM_DELAY_PVE)).WillByDefault(Return(true));

    WorldPacket request(CMSG_RECLAIM_CORPSE);
    request << corpse->GetGUID();
    player->GetSession()->HandleReclaimCorpseOpcode(request);

    EXPECT_FALSE(player->IsAlive());
    EXPECT_TRUE(player->HasPlayerFlag(PLAYER_FLAGS_GHOST));
    EXPECT_EQ(player->GetHealth(), 1u);
    EXPECT_EQ(player->GetCorpse(), corpse);
}

TEST_F(DeathHandlerTest, ForeignCorpseCannotResurrectRequesterWithoutOwnCorpse)
{
    TestPlayer* requester = CreateGhost();
    TestPlayer* owner = CreateGhost(2);
    Corpse* corpse = CreateCorpse(owner);

    WorldPacket request(CMSG_RECLAIM_CORPSE);
    request << corpse->GetGUID();
    requester->GetSession()->HandleReclaimCorpseOpcode(request);

    EXPECT_FALSE(requester->IsAlive());
    EXPECT_TRUE(requester->HasPlayerFlag(PLAYER_FLAGS_GHOST));
    EXPECT_EQ(requester->GetCorpse(), nullptr);
    EXPECT_EQ(owner->GetCorpse(), corpse);
    EXPECT_EQ(corpse->GetOwnerGUID(), owner->GetGUID());
}

TEST_F(DeathHandlerTest, DuplicateReleaseKeepsSingleOwnedCorpse)
{
    TestPlayer* player = CreateGhost();
    Corpse* corpse = CreateCorpse(player);

    for (uint32 attempt = 0; attempt < 2; ++attempt)
    {
        WorldPacket request(CMSG_REPOP_REQUEST);
        request << uint8(0);
        player->GetSession()->HandleRepopRequestOpcode(request);
        EXPECT_EQ(player->GetCorpse(), corpse);
        EXPECT_FALSE(player->IsAlive());
        EXPECT_TRUE(player->HasPlayerFlag(PLAYER_FLAGS_GHOST));
        EXPECT_EQ(player->GetHealth(), 1u);
    }
}

TEST_F(DeathHandlerTest, WrongOffererCannotAcceptPendingResurrection)
{
    TestPlayer* player = CreateGhost();
    ObjectGuid const offerer(uint64(2));
    player->setResurrectRequestData(offerer, 0, 0, 0, 0, 50, 0);

    WorldPacket request(CMSG_RESURRECT_RESPONSE);
    request << ObjectGuid(uint64(3)) << uint8(1);
    player->GetSession()->HandleResurrectResponseOpcode(request);

    EXPECT_FALSE(player->IsAlive());
    EXPECT_TRUE(player->isResurrectRequestedBy(offerer));
    EXPECT_EQ(player->GetHealth(), 1u);
}

TEST_F(DeathHandlerTest, DuplicateResponseCannotResurrectLivingPlayerAgain)
{
    TestPlayer* player = CreateTestPlayer();
    player->SetMaxHealth(100);
    player->SetHealth(50);
    ObjectGuid const offerer(uint64(2));
    player->setResurrectRequestData(offerer, 0, 0, 0, 0, 100, 0);

    for (uint32 attempt = 0; attempt < 2; ++attempt)
    {
        WorldPacket request(CMSG_RESURRECT_RESPONSE);
        request << offerer << uint8(1);
        player->GetSession()->HandleResurrectResponseOpcode(request);
        EXPECT_TRUE(player->IsAlive());
        EXPECT_EQ(player->GetHealth(), 50u);
    }
}

TEST(ResurrectRequestTest, WritesCataNameSicknessTimerAndSpellId)
{
    for (std::string const& name : { std::string(), std::string("Spirit Healer") })
        for (bool sickness : { false, true })
            for (bool useTimer : { false, true })
            {
                WorldPackets::Spells::ResurrectRequest request;
                request.ResurrectOffererGUID = ObjectGuid(uint64(0x0807060504030201));
                request.Name = name;
                request.Sickness = sickness;
                request.UseTimer = useTimer;
                request.SpellID = 2006;
                WorldPacket payload = *request.Write();

                ASSERT_EQ(payload.GetOpcode(), SMSG_RESURRECT_REQUEST);
                ASSERT_EQ(payload.size(), 19u + name.size());
                EXPECT_EQ(payload.read<uint64>(), 0x0807060504030201u);
                EXPECT_EQ(payload.read<uint32>(), name.size() + 1);
                std::string decodedName;
                payload >> decodedName;
                EXPECT_EQ(decodedName, name);
                EXPECT_EQ(payload.read<uint8>(), uint8(sickness));
                EXPECT_EQ(payload.read<uint8>(), uint8(useTimer));
                EXPECT_EQ(payload.read<uint32>(), 2006u);
                EXPECT_EQ(payload.rpos(), payload.size());
            }
}
