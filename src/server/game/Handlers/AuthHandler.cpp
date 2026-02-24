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

#include "Opcodes.h"
#include "WorldPacket.h"
#include "WorldSession.h"

void WorldSession::SendAuthResponse(uint8 code, bool shortForm, uint32 queuePos)
{
    WorldPacket packet(SMSG_AUTH_RESPONSE);
    packet.WriteBit(!shortForm);
    if (!shortForm)
        packet.WriteBit(false); // FCM
    packet.WriteBit(code == AUTH_OK);
    packet.FlushBits();

    if (code == AUTH_OK)
    {
        packet << uint32(0); // TimeRemain
        packet << uint8(Expansion()); // ActiveExpansionLevel
        packet << uint32(0); // TimeSecondsUntilPCKick
        packet << uint8(Expansion()); // AccountExpansionLevel
        packet << uint32(0); // TimeRested
        packet << uint8(0); // TimeOptions
    }

    packet << uint8(code);

    if (!shortForm)
        packet << uint32(queuePos); // QueuePosition

    SendPacket(&packet);
}

void WorldSession::SendClientCacheVersion(uint32 version)
{
    WorldPacket data(SMSG_CLIENTCACHE_VERSION, 4);
    data << uint32(version);
    SendPacket(&data);
}
