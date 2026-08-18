/*
 * This file is part of the AzerothCore Project. See AUTHORS file for Copyright information
 *
 * You may redistribute it and/or modify it under the terms of the GNU General Public License
 * version 2 or, at your option, any later version.
 */

#ifndef SpellPackets_h__
#define SpellPackets_h__

#include "Packet.h"
#include <array>

namespace WorldPackets
{
    namespace Spells
    {
        struct SpellHistoryEntry
        {
            uint32 SpellID = 0;
            uint32 ItemID = 0;
            uint16 Category = 0;
            int32 RecoveryTime = 0;
            int32 CategoryRecoveryTime = 0;
        };

        class SendKnownSpells final : public ServerPacket
        {
        public:
            SendKnownSpells() : ServerPacket(SMSG_SEND_KNOWN_SPELLS, 3) { }

            WorldPacket const* Write() override;

            bool InitialLogin = false;
            std::vector<uint32> KnownSpells;
            std::vector<SpellHistoryEntry> SpellHistoryEntries;
        };

        class SendUnlearnSpells final : public ServerPacket
        {
        public:
            SendUnlearnSpells() : ServerPacket(SMSG_SEND_UNLEARN_SPELLS, 4) { }

            WorldPacket const* Write() override;

            std::vector<uint32> Spells;
        };

        class UpdateActionButtons final : public ServerPacket
        {
        public:
            static std::size_t constexpr NumActionButtons = 144;

            UpdateActionButtons() : ServerPacket(SMSG_UPDATE_ACTION_BUTTONS, NumActionButtons * sizeof(uint32) + 1)
            {
                ActionButtons.fill(0);
            }

            WorldPacket const* Write() override;

            std::array<uint32, NumActionButtons> ActionButtons;
            uint8 Reason = 0;
        };

        struct SpellModifierData
        {
            float ModifierValue = 0.0f;
            uint8 ClassIndex = 0;
        };

        struct SpellModifier
        {
            uint8 ModIndex = 0;
            std::vector<SpellModifierData> ModifierData;
        };

        class SetSpellModifier final : public ServerPacket
        {
        public:
            SetSpellModifier(OpcodeServer opcode) : ServerPacket(opcode, 20) { }

            WorldPacket const* Write() override;

            std::vector<SpellModifier> Modifiers;
        };

        struct ResyncRune
        {
            uint8 RuneType = 0;
            uint8 Cooldown = 0;
        };

        class ResyncRunes final : public ServerPacket
        {
        public:
            ResyncRunes() : ServerPacket(SMSG_RESYNC_RUNES, 4) { }

            WorldPacket const* Write() override;

            std::vector<ResyncRune> Runes;
        };
    }
}

#endif // SpellPackets_h__
