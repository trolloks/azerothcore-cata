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
#include "World.h"
#include "WorldSession.h"

void WorldSession::SendAuthResponse(uint8 code, bool shortForm, uint32 queuePos)
{
    WorldPackets::Auth::AuthResponse packet(code);

    if (code == AUTH_OK)
    {
        WorldPackets::Auth::AuthSuccessInfo& successInfo = packet.SuccessInfo.emplace();
        successInfo.ActiveExpansionLevel = uint8(sWorld->getIntConfig(CONFIG_EXPANSION));
        successInfo.AccountExpansionLevel = Expansion();
    }

    if (!shortForm)
        packet.WaitInfo.emplace(WorldPackets::Auth::AuthWaitInfo{ queuePos, false });

    SendPacket(packet.Write());
}

void WorldSession::SendClientCacheVersion(uint32 version)
{
    WorldPacket data(SMSG_CLIENTCACHE_VERSION, 4);
    data << uint32(version);
    SendPacket(&data);
}
