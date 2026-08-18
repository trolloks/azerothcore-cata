/*
 * This file is part of the AzerothCore Project. See AUTHORS file for Copyright information
 *
 * You may redistribute it and/or modify it under the terms of the GNU General Public License
 * version 2 or, at your option, any later version.
 */

#include "SpellPackets.h"

WorldPacket const* WorldPackets::Spells::SendKnownSpells::Write()
{
    _worldPacket.reserve(1 + 2 + 6 * KnownSpells.size() + 2 + 18 * SpellHistoryEntries.size());
    _worldPacket << uint8(InitialLogin);
    _worldPacket << uint16(KnownSpells.size());

    for (uint32 spellID : KnownSpells)
    {
        _worldPacket << spellID;
        _worldPacket << int16(0);
    }

    _worldPacket << uint16(SpellHistoryEntries.size());
    for (SpellHistoryEntry const& entry : SpellHistoryEntries)
    {
        _worldPacket << entry.SpellID;
        _worldPacket << entry.ItemID;
        _worldPacket << entry.Category;
        _worldPacket << entry.RecoveryTime;
        _worldPacket << entry.CategoryRecoveryTime;
    }

    return &_worldPacket;
}

WorldPacket const* WorldPackets::Spells::SendUnlearnSpells::Write()
{
    _worldPacket << uint32(Spells.size());
    for (uint32 spellID : Spells)
        _worldPacket << spellID;

    return &_worldPacket;
}

WorldPacket const* WorldPackets::Spells::UpdateActionButtons::Write()
{
    _worldPacket.append(ActionButtons.data(), ActionButtons.size());
    _worldPacket << Reason;

    return &_worldPacket;
}

ByteBuffer& operator<<(ByteBuffer& data, WorldPackets::Spells::SpellModifierData const& spellModifierData)
{
    data << spellModifierData.ClassIndex;
    data << spellModifierData.ModifierValue;
    return data;
}

ByteBuffer& operator<<(ByteBuffer& data, WorldPackets::Spells::SpellModifier const& spellModifier)
{
    data << uint32(spellModifier.ModifierData.size());
    data << spellModifier.ModIndex;
    for (WorldPackets::Spells::SpellModifierData const& modifierData : spellModifier.ModifierData)
        data << modifierData;
    return data;
}

WorldPacket const* WorldPackets::Spells::SetSpellModifier::Write()
{
    _worldPacket << uint32(Modifiers.size());
    for (SpellModifier const& modifier : Modifiers)
        _worldPacket << modifier;
    return &_worldPacket;
}

WorldPacket const* WorldPackets::Spells::ResyncRunes::Write()
{
    _worldPacket << uint32(Runes.size());
    for (ResyncRune const& rune : Runes)
    {
        _worldPacket << rune.RuneType;
        _worldPacket << rune.Cooldown;
    }
    return &_worldPacket;
}
