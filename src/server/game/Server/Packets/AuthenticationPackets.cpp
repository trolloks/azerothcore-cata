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

#include "AuthenticationPackets.h"

WorldPacket const* WorldPackets::Auth::AuthChallenge::Write()
{
    _worldPacket.append(DosChallenge.data(), DosChallenge.size());
    _worldPacket.append(Challenge.data(), Challenge.size());
    _worldPacket << DosZeroBits;

    return &_worldPacket;
}

void WorldPackets::Auth::AuthSession::Read()
{
    _worldPacket >> LoginServerID;
    _worldPacket >> BattlegroupID;
    _worldPacket >> LoginServerType;
    _worldPacket >> Digest[10];
    _worldPacket >> Digest[18];
    _worldPacket >> Digest[12];
    _worldPacket >> Digest[5];
    _worldPacket >> DosResponse;
    _worldPacket >> Digest[15];
    _worldPacket >> Digest[9];
    _worldPacket >> Digest[19];
    _worldPacket >> Digest[4];
    _worldPacket >> Digest[7];
    _worldPacket >> Digest[16];
    _worldPacket >> Digest[3];
    _worldPacket >> Build;
    _worldPacket >> Digest[8];
    _worldPacket >> RealmID;
    _worldPacket >> BuildType;
    _worldPacket >> Digest[17];
    _worldPacket >> Digest[6];
    _worldPacket >> Digest[0];
    _worldPacket >> Digest[1];
    _worldPacket >> Digest[11];
    _worldPacket.read(LocalChallenge);
    _worldPacket >> Digest[2];
    _worldPacket >> RegionID;
    _worldPacket >> Digest[14];
    _worldPacket >> Digest[13];

    uint32 addonInfoSize = 0;
    _worldPacket >> addonInfoSize;
    if (addonInfoSize > _worldPacket.size() - _worldPacket.rpos())
        throw ByteBufferPositionException(false, _worldPacket.rpos(), addonInfoSize, _worldPacket.size());

    if (addonInfoSize)
    {
        AddonInfo.resize(addonInfoSize);
        _worldPacket.read(AddonInfo.contents(), addonInfoSize);
    }

    UseIPv6 = _worldPacket.ReadBit();
    uint32 accountLength = _worldPacket.ReadBits(12);
    if (accountLength > _worldPacket.size() - _worldPacket.rpos())
        throw ByteBufferPositionException(false, _worldPacket.rpos(), accountLength, _worldPacket.size());

    Account.resize(accountLength);
    if (accountLength)
        _worldPacket.read(reinterpret_cast<uint8*>(Account.data()), accountLength);

    if (_worldPacket.rpos() != _worldPacket.size())
        throw ByteBufferInvalidValueException("auth session", "trailing bytes");
}

WorldPacket const* WorldPackets::Auth::AuthResponse::Write()
{
    _worldPacket.WriteBit(WaitInfo.has_value());
    if (WaitInfo)
        _worldPacket.WriteBit(WaitInfo->HasFCM);

    _worldPacket.WriteBit(SuccessInfo.has_value());
    _worldPacket.FlushBits();

    if (SuccessInfo)
    {
        _worldPacket << SuccessInfo->TimeRemain;
        _worldPacket << SuccessInfo->ActiveExpansionLevel;
        _worldPacket << SuccessInfo->TimeSecondsUntilPCKick;
        _worldPacket << SuccessInfo->AccountExpansionLevel;
        _worldPacket << SuccessInfo->TimeRested;
        _worldPacket << SuccessInfo->TimeOptions;
    }

    _worldPacket << Result;

    if (WaitInfo)
        _worldPacket << WaitInfo->WaitCount;

    return &_worldPacket;
}
