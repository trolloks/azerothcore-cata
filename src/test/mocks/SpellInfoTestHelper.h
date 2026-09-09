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

#ifndef AZEROTHCORE_SPELL_INFO_TEST_HELPER_H
#define AZEROTHCORE_SPELL_INFO_TEST_HELPER_H

#include "SpellInfo.h"
#include "SharedDefines.h"
#include <memory>

class SpellInfoBuilder
{
public:
    SpellInfoBuilder()
    {
        SpellEntry entry{};
        entry.SchoolMask = SPELL_SCHOOL_MASK_NORMAL;
        entry.Name.fill("");
        entry.NameSubtext.fill("");
        _spell = std::make_unique<SpellInfo>(&entry, std::array<SpellEffectEntry const*, MAX_SPELL_EFFECTS>{});
    }

    SpellInfoBuilder& WithId(uint32 id)
    {
        _spell->Id = id;
        return *this;
    }

    SpellInfoBuilder& WithSpellFamilyName(uint32 familyName)
    {
        _spell->SpellFamilyName = familyName;
        return *this;
    }

    SpellInfoBuilder& WithSpellFamilyFlags(uint32 flag0, uint32 flag1 = 0, uint32 flag2 = 0)
    {
        _spell->SpellFamilyFlags[0] = flag0;
        _spell->SpellFamilyFlags[1] = flag1;
        _spell->SpellFamilyFlags[2] = flag2;
        return *this;
    }

    SpellInfoBuilder& WithSchoolMask(uint32 schoolMask)
    {
        _spell->SchoolMask = schoolMask;
        return *this;
    }

    SpellInfoBuilder& WithProcFlags(uint32 procFlags)
    {
        _spell->ProcFlags = procFlags;
        return *this;
    }

    SpellInfoBuilder& WithProcChance(uint32 procChance)
    {
        _spell->ProcChance = procChance;
        return *this;
    }

    SpellInfoBuilder& WithProcCharges(uint32 procCharges)
    {
        _spell->ProcCharges = procCharges;
        return *this;
    }

    SpellInfoBuilder& WithDmgClass(uint32 dmgClass)
    {
        _spell->DmgClass = dmgClass;
        return *this;
    }

    SpellInfoBuilder& WithAttributesEx3(uint32 attr)
    {
        _spell->AttributesEx3 = attr;
        return *this;
    }

    SpellInfoBuilder& WithAttributesEx(uint32 attr)
    {
        _spell->AttributesEx = attr;
        return *this;
    }

    SpellInfoBuilder& WithEffect(uint8 effIndex, uint32 effect, uint32 auraType = 0)
    {
        if (effIndex < MAX_SPELL_EFFECTS)
        {
            _spell->Effects[effIndex].Effect = effect;
            _spell->Effects[effIndex].ApplyAuraName = AuraType(auraType);
        }
        return *this;
    }

    SpellInfoBuilder& WithEffectImplicitTargets(uint8 effIndex, uint32 targetA, uint32 targetB = 0)
    {
        if (effIndex < MAX_SPELL_EFFECTS)
        {
            _spell->Effects[effIndex].TargetA = SpellImplicitTargetInfo(targetA);
            _spell->Effects[effIndex].TargetB = SpellImplicitTargetInfo(targetB);
        }
        return *this;
    }

    SpellInfoBuilder& WithEffectTriggerSpell(uint8 effIndex, uint32 triggerSpell)
    {
        if (effIndex < MAX_SPELL_EFFECTS)
        {
            _spell->Effects[effIndex].TriggerSpell = triggerSpell;
        }
        return *this;
    }

    SpellInfoBuilder& WithEffectBasePoints(uint8 effIndex, int32 basePoints)
    {
        if (effIndex < MAX_SPELL_EFFECTS)
            _spell->Effects[effIndex].BasePoints = basePoints;
        return *this;
    }

    SpellInfoBuilder& WithEffectMiscValue(uint8 effIndex, int32 miscValue)
    {
        if (effIndex < MAX_SPELL_EFFECTS)
            _spell->Effects[effIndex].MiscValue = miscValue;
        return *this;
    }

    SpellInfoBuilder& WithEffectDieSides(uint8 effIndex, int32 dieSides)
    {
        if (effIndex < MAX_SPELL_EFFECTS)
            _spell->Effects[effIndex].DieSides = dieSides;
        return *this;
    }

    SpellInfoBuilder& WithAttributes(uint32 attr)
    {
        _spell->Attributes = attr;
        return *this;
    }

    SpellInfo* Build()
    {
        return _spell.release();
    }

    std::unique_ptr<SpellInfo> BuildUnique()
    {
        return std::move(_spell);
    }

private:
    std::unique_ptr<SpellInfo> _spell;
};

#endif // AZEROTHCORE_SPELL_INFO_TEST_HELPER_H
