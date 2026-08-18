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

#include "CharacterPackets.h"

void WorldPackets::Character::EnumCharacters::Read()
{
    if (_worldPacket.rpos() != _worldPacket.size())
        throw ByteBufferInvalidValueException("character enumeration", "trailing bytes");
}

WorldPacket const* WorldPackets::Character::EnumCharactersResult::Write()
{
    _worldPacket.reserve(6 + Characters.size() * sizeof(CharacterInfo) +
        FactionChangeRestrictions.size() * sizeof(RestrictedFactionChangeRuleInfo));

    _worldPacket.WriteBits(FactionChangeRestrictions.size(), 23);
    _worldPacket.WriteBit(Success);
    _worldPacket.WriteBits(Characters.size(), 17);

    for (CharacterInfo const& charInfo : Characters)
    {
        _worldPacket.WriteBit(charInfo.Guid[3]);
        _worldPacket.WriteBit(charInfo.GuildGUID[1]);
        _worldPacket.WriteBit(charInfo.GuildGUID[7]);
        _worldPacket.WriteBit(charInfo.GuildGUID[2]);
        _worldPacket.WriteBits(charInfo.Name.length(), 7);
        _worldPacket.WriteBit(charInfo.Guid[4]);
        _worldPacket.WriteBit(charInfo.Guid[7]);
        _worldPacket.WriteBit(charInfo.GuildGUID[3]);
        _worldPacket.WriteBit(charInfo.Guid[5]);
        _worldPacket.WriteBit(charInfo.Guid[6]);
        _worldPacket.WriteBit(charInfo.Guid[1]);
        _worldPacket.WriteBit(charInfo.GuildGUID[5]);
        _worldPacket.WriteBit(charInfo.GuildGUID[4]);
        _worldPacket.WriteBit(charInfo.FirstLogin);
        _worldPacket.WriteBit(charInfo.Guid[0]);
        _worldPacket.WriteBit(charInfo.Guid[2]);
        _worldPacket.WriteBit(charInfo.Guid[6]);
        _worldPacket.WriteBit(charInfo.GuildGUID[0]);
    }

    _worldPacket.FlushBits();

    for (CharacterInfo const& charInfo : Characters)
    {
        _worldPacket << uint8(charInfo.ClassID);

        for (CharacterInfo::VisualItemInfo const& visualItem : charInfo.VisualItems)
        {
            _worldPacket << uint8(visualItem.InvType);
            _worldPacket << uint32(visualItem.DisplayID);
            _worldPacket << uint32(visualItem.DisplayEnchantID);
        }

        _worldPacket << uint32(charInfo.PetCreatureFamilyID);
        _worldPacket.WriteByteSeq(charInfo.GuildGUID[2]);
        _worldPacket << uint8(charInfo.ListPosition);
        _worldPacket << uint8(charInfo.HairStyle);
        _worldPacket.WriteByteSeq(charInfo.GuildGUID[3]);
        _worldPacket << uint32(charInfo.PetCreatureDisplayID);
        _worldPacket << uint32(charInfo.Flags);
        _worldPacket << uint8(charInfo.HairColor);
        _worldPacket.WriteByteSeq(charInfo.Guid[4]);
        _worldPacket << int32(charInfo.MapID);
        _worldPacket.WriteByteSeq(charInfo.GuildGUID[5]);
        _worldPacket << float(charInfo.PreloadPos.GetPositionZ());
        _worldPacket.WriteByteSeq(charInfo.GuildGUID[6]);
        _worldPacket << uint32(charInfo.PetExperienceLevel);
        _worldPacket.WriteByteSeq(charInfo.Guid[3]);
        _worldPacket << float(charInfo.PreloadPos.GetPositionY());
        _worldPacket << uint32(charInfo.Flags2);
        _worldPacket << uint8(charInfo.FacialHair);
        _worldPacket.WriteByteSeq(charInfo.Guid[7]);
        _worldPacket << uint8(charInfo.SexID);
        _worldPacket.WriteString(charInfo.Name);
        _worldPacket << uint8(charInfo.FaceID);
        _worldPacket.WriteByteSeq(charInfo.Guid[0]);
        _worldPacket.WriteByteSeq(charInfo.Guid[2]);
        _worldPacket.WriteByteSeq(charInfo.GuildGUID[1]);
        _worldPacket.WriteByteSeq(charInfo.GuildGUID[7]);
        _worldPacket << float(charInfo.PreloadPos.GetPositionX());
        _worldPacket << uint8(charInfo.SkinID);
        _worldPacket << uint8(charInfo.RaceID);
        _worldPacket << uint8(charInfo.ExperienceLevel);
        _worldPacket.WriteByteSeq(charInfo.Guid[6]);
        _worldPacket.WriteByteSeq(charInfo.GuildGUID[4]);
        _worldPacket.WriteByteSeq(charInfo.GuildGUID[0]);
        _worldPacket.WriteByteSeq(charInfo.Guid[5]);
        _worldPacket.WriteByteSeq(charInfo.Guid[1]);
        _worldPacket << int32(charInfo.ZoneID);
    }

    for (RestrictedFactionChangeRuleInfo const& rule : FactionChangeRestrictions)
    {
        _worldPacket << int32(rule.Mask);
        _worldPacket << uint8(rule.Race);
    }

    return &_worldPacket;
}

void WorldPackets::Character::PlayerLogin::Read()
{
    Guid[2] = _worldPacket.ReadBit();
    Guid[3] = _worldPacket.ReadBit();
    Guid[0] = _worldPacket.ReadBit();
    Guid[6] = _worldPacket.ReadBit();
    Guid[4] = _worldPacket.ReadBit();
    Guid[5] = _worldPacket.ReadBit();
    Guid[1] = _worldPacket.ReadBit();
    Guid[7] = _worldPacket.ReadBit();

    _worldPacket.ReadByteSeq(Guid[2]);
    _worldPacket.ReadByteSeq(Guid[7]);
    _worldPacket.ReadByteSeq(Guid[0]);
    _worldPacket.ReadByteSeq(Guid[3]);
    _worldPacket.ReadByteSeq(Guid[5]);
    _worldPacket.ReadByteSeq(Guid[6]);
    _worldPacket.ReadByteSeq(Guid[1]);
    _worldPacket.ReadByteSeq(Guid[4]);
}

void WorldPackets::Character::ShowingCloak::Read()
{
    _worldPacket >> ShowCloak;
}

void WorldPackets::Character::ShowingHelm::Read()
{
    _worldPacket >> ShowHelm;
}

WorldPacket const* WorldPackets::Character::LogoutResponse::Write()
{
    _worldPacket << uint32(LogoutResult);
    _worldPacket << uint8(Instant);
    return &_worldPacket;
}

void WorldPackets::Character::PlayedTimeClient::Read()
{
    _worldPacket >> TriggerScriptEvent;
}

WorldPacket const* WorldPackets::Character::PlayedTime::Write()
{
    _worldPacket << uint32(TotalTime);
    _worldPacket << uint32(LevelTime);
    _worldPacket << uint8(TriggerScriptEvent);

    return &_worldPacket;
}

WorldPacket const* WorldPackets::Character::LoginVerifyWorld::Write()
{
    _worldPacket << int32(MapID);
    _worldPacket << float(Pos.GetPositionX());
    _worldPacket << float(Pos.GetPositionY());
    _worldPacket << float(Pos.GetPositionZ());
    _worldPacket << float(Pos.GetOrientation());

    return &_worldPacket;
}
