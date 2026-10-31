/*
 * This file is part of the AzerothCore Project. See AUTHORS file for Copyright information
 *
 * This program is free software; you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the
 * Free Software Foundation; either version 2 of the License, or (at your
 * option) any later version.
 *
 * This program is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 * FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
 * more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program. If not, see <http://www.gnu.org/licenses/>.
 */

#ifndef ACORE_GENERICMOVEMENTGENERATOR_H
#define ACORE_GENERICMOVEMENTGENERATOR_H

#include "MovementGenerator.h"
#include "MoveSplineInit.h"
#include "Timer.h"

class Unit;

class GenericMovementGenerator : public MovementGenerator
{
public:
    GenericMovementGenerator(Movement::MoveSplineInit&& splineInit, MovementGeneratorType type, uint32 id)
        : _splineInit(std::move(splineInit)), _type(type), _pointId(id), _duration(0), _arrived(false) { }

    void Initialize(Unit* owner) override;
    void Finalize(Unit* owner) override;
    void Reset(Unit*) override { }
    bool Update(Unit* owner, uint32 diff) override;
    MovementGeneratorType GetMovementGeneratorType() override { return _type; }

private:
    void MovementInform(Unit* owner);

    Movement::MoveSplineInit _splineInit;
    MovementGeneratorType _type;
    uint32 _pointId;
    TimeTrackerSmall _duration;
    bool _arrived;
};

#endif
