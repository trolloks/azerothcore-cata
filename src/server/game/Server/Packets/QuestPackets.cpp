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

#include "QuestPackets.h"

void WorldPackets::Quest::QuestRewards::Write(ByteBuffer& data) const
{
    data << ChoiceItemCount;
    for (uint32 value : ChoiceItemId)
        data << value;
    for (uint32 value : ChoiceItemQuantity)
        data << value;
    for (uint32 value : ChoiceItemDisplay)
        data << value;
    data << ItemCount;
    for (uint32 value : ItemId)
        data << value;
    for (uint32 value : ItemQuantity)
        data << value;
    for (uint32 value : ItemDisplay)
        data << value;
    data << Money << Experience << Title << uint32(0) << float(0) << BonusTalents << uint32(0) << FactionFlags;
    for (uint32 value : FactionId)
        data << value;
    for (int32 value : FactionValue)
        data << value;
    for (uint32 value : FactionOverride)
        data << value;
    data << DisplaySpell << Spell;
    for (uint32 value : CurrencyId)
        data << value;
    for (uint32 value : CurrencyQuantity)
        data << value;
    data << SkillLine << SkillUps;
}

WorldPacket const* WorldPackets::Quest::QuestUpdateComplete::Write()
{
    _worldPacket << QuestId;

    return &_worldPacket;
}

WorldPacket const* WorldPackets::Quest::QuestGiverQuestComplete::Write()
{
    _worldPacket << RewardTalents;
    _worldPacket << RewardSkillUps;
    _worldPacket << RewardMoney;
    _worldPacket << Experience;
    _worldPacket << QuestId;
    _worldPacket << RewardSkillLine;
    _worldPacket.WriteBit(LaunchGossip);
    _worldPacket.WriteBit(UseQuestReward);
    _worldPacket.FlushBits();

    return &_worldPacket;
}

WorldPacket const* WorldPackets::Quest::QuestGiverQuestFailed::Write()
{
    _worldPacket << QuestId;
    _worldPacket << FailureReason;

    return &_worldPacket;
}

WorldPacket const* WorldPackets::Quest::QuestUpdateFailedTimer::Write()
{
    _worldPacket << QuestId;

    return &_worldPacket;
}

WorldPacket const* WorldPackets::Quest::QuestGiverQuestInvalid::Write()
{
    _worldPacket << FailureReason;

    return &_worldPacket;
}

WorldPacket const* WorldPackets::Quest::QuestConfirmAccept::Write()
{
    _worldPacket << QuestId;
    _worldPacket << QuestTitle;
    _worldPacket << PlayerGuid;

    return &_worldPacket;
}

WorldPacket const* WorldPackets::Quest::QuestPushResult::Write()
{
    _worldPacket << PlayerGuid;
    _worldPacket << QuestShareMessage;

    return &_worldPacket;
}

WorldPacket const* WorldPackets::Quest::QuestUpdateAddKill::Write()
{
    _worldPacket << QuestId;
    _worldPacket << CreatureEntry;
    _worldPacket << CurrentCount;
    _worldPacket << RequiredCount;
    _worldPacket << ObjectiveGuid;

    return &_worldPacket;
}

WorldPacket const* WorldPackets::Quest::QuestUpdateAddPvPKill::Write()
{
    _worldPacket << QuestId;
    _worldPacket << CurrentCount;
    _worldPacket << RequiredCount;

    return &_worldPacket;
}

void WorldPackets::Quest::QuestPushResultClient::Read()
{
    _worldPacket >> PlayerGuid;
    _worldPacket >> QuestId;
    uint8 Message;
    _worldPacket >> Message;
    QuestShareMessage = static_cast<QuestShareMessages>(Message);
}

void WorldPackets::Quest::QuestLogSwapQuest::Read()
{
    _worldPacket >> Slot1;
    _worldPacket >> Slot2;
}

void WorldPackets::Quest::QuestLogRemoveQuest::Read()
{
    _worldPacket >> Slot;
}

void WorldPackets::Quest::QuestConfirmAcceptClient::Read()
{
    _worldPacket >> QuestId;
}

void WorldPackets::Quest::PushQuestToParty::Read()
{
    _worldPacket >> QuestId;
}
