/*
 * This file is part of the AzerothCore Project. See AUTHORS file for Copyright information
 *
 * You may redistribute it and/or modify it under the terms of the GNU General Public License
 * version 2 or, at your option, any later version.
 */

#ifndef MovementPackets_h__
#define MovementPackets_h__

#include "ObjectGuid.h"
#include "Packet.h"

namespace WorldPackets
{
    namespace Movement
    {
        class MoveSetActiveMover final : public ServerPacket
        {
        public:
            MoveSetActiveMover(ObjectGuid moverGuid) : ServerPacket(SMSG_MOVE_SET_ACTIVE_MOVER, 8), MoverGUID(moverGuid) { }

            WorldPacket const* Write() override;

            ObjectGuid MoverGUID;
        };
    }
}

#endif // MovementPackets_h__
