/*
 * This file is part of the AzerothCore Project. See AUTHORS file for Copyright information
 *
 * This program is free software; you can redistribute it and/or modify it
 * under the terms of the GNU Affero General Public License as published by the
 * Free Software Foundation; either version 3 of the License, or (at your
 * option) any later version.
 *
 * This program is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 * FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License for
 * more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program. If not, see <http://www.gnu.org/licenses/>.
 */

#include "Creature.h"
#include "ItemTemplate.h"
#include "NPCPackets.h"
#include "gtest/gtest.h"

TEST(NPCPacketsTest, VendorInventoryWritesCataLayout)
{
    EXPECT_EQ(uint16(SMSG_VENDOR_INVENTORY), 0x7CB0);

    WorldPackets::NPC::VendorInventory packet;
    packet.Vendor = ObjectGuid::Create<HighGuid::Player>(1); // packed guid: mask 0x01, one byte 0x01

    WorldPackets::NPC::VendorItem item;
    item.MuID = 1;
    item.Type = ITEM_VENDOR_TYPE_ITEM;
    item.ItemID = 0xABCD;
    item.ItemDisplayInfoID = 7;
    item.Quantity = -1;
    item.Price = 100;
    item.StackCount = 1;
    packet.Items.push_back(item);

    WorldPacket const* written = packet.Write();
    // Bits: vendor bytes 1, 0, the 21-bit item count, vendor bytes 3, 6, 5, 2, 7, then per item the
    // absent-bit of ExtendedCostID and PlayerConditionFailed, then vendor byte 4, flushed to one byte.
    uint8 const expected[] = {
        0x40, 0x00, 0x02, 0x0C,
        0x01, 0x00, 0x00, 0x00,                 // MuID
        0x00, 0x00, 0x00, 0x00,                 // Durability
        0xCD, 0xAB, 0x00, 0x00,                 // ItemID
        0x01, 0x00, 0x00, 0x00,                 // Type
        0x64, 0x00, 0x00, 0x00,                 // Price
        0x07, 0x00, 0x00, 0x00,                 // ItemDisplayInfoID
        0xFF, 0xFF, 0xFF, 0xFF,                 // Quantity == -1, unlimited stock
        0x01, 0x00, 0x00, 0x00,                 // StackCount
        0x00,                                   // vendor byte 0 written as 1 ^ 1
        0x00                                    // reason
    };
    ASSERT_EQ(written->size(), sizeof(expected));
    for (std::size_t index = 0; index < sizeof(expected); ++index)
        EXPECT_EQ(written->contents()[index], expected[index]) << index;
}

TEST(NPCPacketsTest, VendorInventoryWritesExtendedCostAndEmptyReason)
{
    WorldPackets::NPC::VendorInventory packet;
    packet.Vendor = ObjectGuid::Create<HighGuid::Player>(1);
    packet.Reason = VENDOR_INVENTORY_REASON_INVENTORY_EMPTY;

    WorldPackets::NPC::VendorItem item;
    item.MuID = 1;
    item.Type = ITEM_VENDOR_TYPE_ITEM;
    item.ItemID = 0xABCD;
    item.ItemDisplayInfoID = 7;
    item.Quantity = -1;
    item.Price = 100;
    item.StackCount = 1;
    item.ExtendedCostID = 2686;
    packet.Items.push_back(item);

    WorldPacket const* written = packet.Write();
    uint8 const expected[] = {
        0x40, 0x00, 0x02, 0x04,                 // present ExtendedCostID clears its absent-bit
        0x01, 0x00, 0x00, 0x00,                 // MuID
        0x00, 0x00, 0x00, 0x00,                 // Durability
        0x7E, 0x0A, 0x00, 0x00,                 // ExtendedCostID
        0xCD, 0xAB, 0x00, 0x00,                 // ItemID
        0x01, 0x00, 0x00, 0x00,                 // Type
        0x64, 0x00, 0x00, 0x00,                 // Price
        0x07, 0x00, 0x00, 0x00,                 // ItemDisplayInfoID
        0xFF, 0xFF, 0xFF, 0xFF,                 // Quantity
        0x01, 0x00, 0x00, 0x00,                 // StackCount
        0x00,                                   // vendor byte 0 written as 1 ^ 1
        0x01                                    // reason
    };
    ASSERT_EQ(written->size(), sizeof(expected));
    for (std::size_t index = 0; index < sizeof(expected); ++index)
        EXPECT_EQ(written->contents()[index], expected[index]) << index;
}

TEST(NPCPacketsTest, VendorInventoryWritesEmptyInventoryReason)
{
    WorldPackets::NPC::VendorInventory packet;
    packet.Vendor = ObjectGuid::Create<HighGuid::Player>(1);
    packet.Reason = VENDOR_INVENTORY_REASON_INVENTORY_EMPTY;

    WorldPacket const* written = packet.Write();
    uint8 const expected[] = {
        0x40, 0x00, 0x00, 0x00,                 // 21-bit item count == 0, no per-item bits
        0x00,                                   // vendor byte 0 written as 1 ^ 1
        0x01                                    // reason
    };
    ASSERT_EQ(written->size(), sizeof(expected));
    for (std::size_t index = 0; index < sizeof(expected); ++index)
        EXPECT_EQ(written->contents()[index], expected[index]) << index;
}
