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
    // absent-bit of ExtendedCostID and PlayerConditionFailed, then vendor byte 4; the bits run on across the
    // header bytes and FlushBits pads the last partial one, so the header is four bytes here.
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

TEST(NPCPacketsTest, VendorInventoryWritesMultipleRowsAndCreatureGuid)
{
    WorldPackets::NPC::VendorInventory packet;
    // Godric Rothgar's spawn, the vendor the plan 33 acceptance uses; vendor byte 5 is zero and is dropped
    // by the byte-sequence writer, every other guid byte is non-zero
    packet.Vendor = ObjectGuid::Create<HighGuid::Unit>(1213, 79952);

    WorldPackets::NPC::VendorItem first;
    first.MuID = 1;
    first.Durability = 55;
    first.ExtendedCostID = 2686;
    first.ItemID = 0xABCD;
    first.Type = ITEM_VENDOR_TYPE_ITEM;
    first.Price = 250;
    first.ItemDisplayInfoID = 7;
    first.Quantity = 3;         // finite stock
    first.StackCount = 2;
    packet.Items.push_back(first);

    WorldPackets::NPC::VendorItem second;
    second.MuID = 2;
    second.ItemID = 0x1234;
    second.Type = ITEM_VENDOR_TYPE_ITEM;
    second.Price = 0;
    second.ItemDisplayInfoID = 9;
    second.Quantity = -1;       // unlimited stock
    second.StackCount = 1;
    packet.Items.push_back(second);

    WorldPacket const* written = packet.Write();
    // Header: the vendor bytes 1 and 0, the 21-bit item count, the vendor bytes 3, 6, 5, 2 and 7 (each as
    // "the byte is non-zero", so byte 5's bit is 0), one absent bit per optional field per item, then vendor
    // byte 4, flushed to 5 bytes. Body: both item bodies in row order, the vendor byte sequences in the order
    // 5,4,1,0,6 with the zero byte 5 dropped entirely, the reason byte, then 2,3,7.
    uint8 const expected[] = {
        0xC0, 0x00, 0x05, 0xB7, 0x80,
        0x01, 0x00, 0x00, 0x00,                 // MuID 1
        0x37, 0x00, 0x00, 0x00,                 // Durability 55
        0x7E, 0x0A, 0x00, 0x00,                 // ExtendedCostID 2686
        0xCD, 0xAB, 0x00, 0x00,                 // ItemID 0xABCD
        0x01, 0x00, 0x00, 0x00,                 // Type
        0xFA, 0x00, 0x00, 0x00,                 // Price 250
        0x07, 0x00, 0x00, 0x00,                 // ItemDisplayInfoID 7
        0x03, 0x00, 0x00, 0x00,                 // Quantity 3
        0x02, 0x00, 0x00, 0x00,                 // StackCount 2
        0x02, 0x00, 0x00, 0x00,                 // MuID 2
        0x00, 0x00, 0x00, 0x00,                 // Durability 0
        0x34, 0x12, 0x00, 0x00,                 // ItemID 0x1234, no extended cost field
        0x01, 0x00, 0x00, 0x00,                 // Type
        0x00, 0x00, 0x00, 0x00,                 // Price 0
        0x09, 0x00, 0x00, 0x00,                 // ItemDisplayInfoID 9
        0xFF, 0xFF, 0xFF, 0xFF,                 // Quantity == -1, unlimited stock
        0x01, 0x00, 0x00, 0x00,                 // StackCount 1
        0x05, 0x39, 0x51, 0x31,                 // vendor bytes 4, 1, 0, 6 (byte 5 is zero, dropped)
        0x00,                                   // reason, no inventory-empty flag
        0x00, 0xBC, 0xF0                        // vendor bytes 2, 3, 7
    };
    ASSERT_EQ(written->size(), sizeof(expected));
    for (std::size_t index = 0; index < sizeof(expected); ++index)
        EXPECT_EQ(written->contents()[index], expected[index]) << index;
}

TEST(NPCPacketsTest, VendorInventoryWritesBothOptionalFieldsAndFullGuid)
{
    WorldPackets::NPC::VendorInventory packet;
    // every byte of this guid is non-zero, so all eight byte sequences are written and none is dropped
    packet.Vendor.Set(0x0807060504030201ULL);

    WorldPackets::NPC::VendorItem first;
    first.MuID = 1;
    first.Durability = 99;
    first.ExtendedCostID = 2686;
    first.ItemID = 0xABCD;
    first.Type = ITEM_VENDOR_TYPE_ITEM;
    first.Price = 100;
    first.ItemDisplayInfoID = 7;
    // AC never sets this one (no PlayerCondition.dbc store), the position still has to be written when present
    first.PlayerConditionFailed = 123;
    first.Quantity = 5;
    first.StackCount = 3;
    packet.Items.push_back(first);

    WorldPackets::NPC::VendorItem second;
    second.MuID = 2;
    second.ItemID = 0x1234;
    second.Type = ITEM_VENDOR_TYPE_ITEM;
    second.Price = 0;
    second.ItemDisplayInfoID = 9;
    second.Quantity = -1;       // unlimited stock
    second.StackCount = 1;
    packet.Items.push_back(second);

    WorldPacket const* written = packet.Write();
    uint8 const expected[] = {
        0xC0, 0x00, 0x05, 0xF3, 0x80,
        0x01, 0x00, 0x00, 0x00,                 // MuID 1
        0x63, 0x00, 0x00, 0x00,                 // Durability 99
        0x7E, 0x0A, 0x00, 0x00,                 // ExtendedCostID 2686
        0xCD, 0xAB, 0x00, 0x00,                 // ItemID 0xABCD
        0x01, 0x00, 0x00, 0x00,                 // Type
        0x64, 0x00, 0x00, 0x00,                 // Price 100
        0x07, 0x00, 0x00, 0x00,                 // ItemDisplayInfoID 7
        0x7B, 0x00, 0x00, 0x00,                 // PlayerConditionFailed 123
        0x05, 0x00, 0x00, 0x00,                 // Quantity 5
        0x03, 0x00, 0x00, 0x00,                 // StackCount 3
        0x02, 0x00, 0x00, 0x00,                 // MuID 2
        0x00, 0x00, 0x00, 0x00,                 // Durability 0
        0x34, 0x12, 0x00, 0x00,                 // ItemID 0x1234, no extended cost field
        0x01, 0x00, 0x00, 0x00,                 // Type
        0x00, 0x00, 0x00, 0x00,                 // Price 0
        0x09, 0x00, 0x00, 0x00,                 // ItemDisplayInfoID 9
        0xFF, 0xFF, 0xFF, 0xFF,                 // Quantity == -1, unlimited stock
        0x01, 0x00, 0x00, 0x00,                 // StackCount 1
        0x07, 0x04, 0x03, 0x00,                 // vendor bytes 5, 4, 1, 0
        0x06,                                   // vendor byte 6
        0x00,                                   // reason, no inventory-empty flag
        0x02, 0x05, 0x09                        // vendor bytes 2, 3, 7
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
