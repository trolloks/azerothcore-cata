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

#ifndef AC_MOVESPLINEFLAG_H
#define AC_MOVESPLINEFLAG_H

#include "MovementTypedefs.h"
#include <string>

namespace Movement
{
#if defined( __GNUC__ )
#pragma pack(1)
#else
#pragma pack(push, 1)
#endif

    class MoveSplineFlag
    {
    public:
        // Numeric values match Cataclysm build 15595 (see TrinityCore-Cata commit
        // 0e8605edb84e3eb2e1a18f695f33bbdc6488b4ca, "sync spline code which is relevant for
        // Cataclysm with master branch"). WotLK stored the animation id in the low flag byte and
        // the final-facing type as flag bits; build 15595 does neither, so Final_Point/Target/Angle
        // below are this fork's own bookkeeping only (never sent as part of the flags word, see
        // Mask_No_Monster_Move) and reuse bit positions the client build doesn't verify.
        enum eFlags
        {
            None                = 0x00000000,
            Unknown1            = 0x00000001,           // NOT VERIFIED
            Unknown2            = 0x00000002,           // NOT VERIFIED
            Unknown4            = 0x00000004,           // NOT VERIFIED
            OrientationInversed = 0x00000008,           // NOT VERIFIED - related to falling/fixed orientation
            FallingSlow         = 0x00000010,
            Done                = 0x00000020,
            Falling             = 0x00000040,           // Affects elevation computation, can't be combined with Parabolic flag
            No_Spline           = 0x00000080,
            Unknown100          = 0x00000100,           // NOT VERIFIED
            Flying              = 0x00000200,           // Smooth movement(Catmullrom interpolation mode), flying animation
            OrientationFixed    = 0x00000400,           // Model orientation fixed
            Catmullrom          = 0x00000800,           // Used Catmullrom interpolation mode
            Cyclic              = 0x00001000,           // Movement by cycled spline
            Enter_Cycle         = 0x00002000,           // Everytimes appears with cyclic flag in monster move packet, erases first spline vertex after first cycle done
            Frozen              = 0x00004000,           // Will never arrive
            TransportEnter      = 0x00008000,
            TransportExit       = 0x00010000,
            Unknown20000        = 0x00020000,           // NOT VERIFIED
            Unknown40000        = 0x00040000,           // NOT VERIFIED
            Backward            = 0x00080000,
            SmoothGroundPath    = 0x00100000,
            CanSwim             = 0x00200000,
            UncompressedPath    = 0x00400000,
            Unknown800000       = 0x00800000,           // NOT VERIFIED
            Animation           = 0x01000000,           // Plays animation after some time passed
            Parabolic           = 0x02000000,           // Affects elevation computation, can't be combined with Falling flag
            FadeObject          = 0x04000000,
            Steering            = 0x08000000,
            UnlimitedSpeed      = 0x10000000,
            Final_Point         = 0x20000000,           // fork-local only, see comment above
            Final_Target        = 0x40000000,           // fork-local only, see comment above
            Final_Angle         = 0x80000000,           // fork-local only, see comment above

            // Masks
            Mask_Final_Facing   = Final_Point | Final_Target | Final_Angle,
            // flags that shouldn't be appended into SMSG_ON_MONSTER_MOVE\SMSG_ON_MONSTER_MOVE_TRANSPORT packet, should be more probably
            Mask_No_Monster_Move = Mask_Final_Facing | Done,
            // CatmullRom interpolation mode used
            Mask_CatmullRom     = Flying | Catmullrom,
            // Unused, not supported flags
            Mask_Unused         = No_Spline | Enter_Cycle | Frozen | Unknown1 | Unknown2 | Unknown4 | Unknown100 | Unknown20000
                                | Unknown40000 | Unknown800000 | Backward | SmoothGroundPath | FadeObject
                                | Steering | UnlimitedSpeed
        };

        inline uint32& raw() { return (uint32&) * this; }
        [[nodiscard]] inline uint32 const& raw() const { return (uint32 const&) * this; }

        MoveSplineFlag() { raw() = 0; }
        MoveSplineFlag(uint32 f) { raw() = f; }
        MoveSplineFlag(MoveSplineFlag const& f) { raw() = f.raw(); animId = f.animId; }
        MoveSplineFlag(MoveSplineFlag&&) = default;
        MoveSplineFlag& operator=(MoveSplineFlag const&) = default;
        MoveSplineFlag& operator=(MoveSplineFlag&&) = default;

        // Constant interface

        [[nodiscard]] bool isSmooth() const { return raw() & Mask_CatmullRom; }
        [[nodiscard]] bool isLinear() const { return !isSmooth(); }
        [[nodiscard]] bool isFacing() const { return raw() & Mask_Final_Facing; }

        [[nodiscard]] bool hasAllFlags(uint32 f) const { return (raw() & f) == f; }
        [[nodiscard]] bool hasFlag(uint32 f) const { return (raw() & f) != 0; }
        uint32 operator & (uint32 f) const { return (raw() & f); }
        uint32 operator | (uint32 f) const { return (raw() | f); }
        [[nodiscard]] std::string ToString() const;

        // Not constant interface

        void operator &= (uint32 f) { raw() &= f; }
        void operator |= (uint32 f) { raw() |= f; }

        void EnableAnimation() { raw() = (raw() & ~(Falling | Parabolic)) | Animation; }
        void EnableParabolic() { raw() = (raw() & ~(Falling | Animation)) | Parabolic; }
        void EnableFalling() { raw() = (raw() & ~(Parabolic | Flying | Animation)) | Falling; }
        void EnableFlying() { raw() = (raw() & ~(Falling | Catmullrom)) | Flying; }
        void EnableCatmullRom() { raw() = (raw() & ~Flying) | Catmullrom; }
        void EnableFacingPoint() { raw() = (raw() & ~Mask_Final_Facing) | Final_Point; }
        void EnableFacingAngle() { raw() = (raw() & ~Mask_Final_Facing) | Final_Angle; }
        void EnableFacingTarget() { raw() = (raw() & ~Mask_Final_Facing) | Final_Target; }
        void EnableTransportEnter() { raw() = (raw() & ~TransportExit) | TransportEnter; }
        void EnableTransportExit() { raw() = (raw() & ~TransportEnter) | TransportExit; }

        bool unknown1            : 1;
        bool unknown2            : 1;
        bool unknown4            : 1;
        bool orientationInversed : 1;
        bool fallingSlow         : 1;
        bool done                : 1;
        bool falling             : 1;
        bool no_spline           : 1;
        bool unknown100          : 1;
        bool flying              : 1;
        bool orientationFixed    : 1;
        bool catmullrom          : 1;
        bool cyclic              : 1;
        bool enter_cycle         : 1;
        bool frozen              : 1;
        bool transportEnter      : 1;
        bool transportExit       : 1;
        bool unknown20000        : 1;
        bool unknown40000        : 1;
        bool backward            : 1;
        bool smoothGroundPath    : 1;
        bool canSwim             : 1;
        bool uncompressedPath    : 1;
        bool unknown800000       : 1;
        bool animation           : 1;
        bool parabolic           : 1;
        bool fadeObject          : 1;
        bool steering            : 1;
        bool unlimitedSpeed      : 1;
        bool final_point         : 1;
        bool final_target        : 1;
        bool final_angle         : 1;

        // Not part of the wire flags word in build 15595; SMSG_ON_MONSTER_MOVE sends this separately
        // (see MovementPacketBuilder::WriteCommonMonsterMovePart).
        uint8 animId = 0;
    };
#if defined( __GNUC__ )
#pragma pack()
#else
#pragma pack(pop)
#endif
}

#endif // AC_MOVESPLINEFLAG_H
