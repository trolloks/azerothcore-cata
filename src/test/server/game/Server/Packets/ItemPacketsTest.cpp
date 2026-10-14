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

#include "ItemPackets.h"
#include "IntegrationTestFixture.h"
#include "Item.h"
#include "ObjectMgr.h"
#include "gtest/gtest.h"

TEST(ItemPacketsTest, DestroyReadsCataCountWithoutTruncation)
{
    EXPECT_EQ(uint16(CMSG_DESTROYITEM), 0x4A27);
    WorldPacket raw(CMSG_DESTROYITEM, 6);
    raw << uint8(255) << uint8(38) << uint32(0x04030201);
    WorldPackets::Item::DestroyItem packet(std::move(raw));
    packet.Read();
    EXPECT_EQ(packet.Bag, 255);
    EXPECT_EQ(packet.Slot, 38);
    EXPECT_EQ(packet.Count, 0x04030201u);
    EXPECT_EQ(packet.GetRawPacket()->rpos(), packet.GetSize());
}

TEST(ItemPacketsTest, DestroyRejectsTruncatedCataCount)
{
    WorldPacket raw(CMSG_DESTROYITEM, 3);
    raw << uint8(255) << uint8(38) << uint8(1);
    WorldPackets::Item::DestroyItem packet(std::move(raw));
    EXPECT_THROW(packet.Read(), ByteBufferException);
}

class ItemInventoryTest : public IntegrationTestFixture
{
protected:
    void SetUp() override
    {
        IntegrationTestFixture::SetUp();
        _templates = const_cast<std::vector<ItemTemplate*>*>(sObjectMgr->GetItemTemplateStoreFast());
        _originalSize = _templates->size();
        if (_templates->size() <= 1)
            _templates->resize(2);
        _originalTemplate = (*_templates)[1];
        _template.ItemId = 1;
        _template.Class = ITEM_CLASS_MISCELLANEOUS;
        _template.AllowableClass = -1;
        _template.AllowableRace = -1;
        _template.Stackable = 1;
        (*_templates)[1] = &_template;
        _player = CreateTestPlayer();
        _player->setRace(RACE_HUMAN);
        _player->SetByteValue(UNIT_FIELD_BYTES_0, 1, CLASS_WARRIOR);
        _player->SetLevel(1);
    }

    void TearDown() override
    {
        (*_templates)[1] = _originalTemplate;
        _templates->resize(_originalSize);
        IntegrationTestFixture::TearDown();
    }

    ItemTemplate _template{};
    TestPlayer* _player = nullptr;
    std::vector<ItemTemplate*>* _templates = nullptr;
    ItemTemplate* _originalTemplate = nullptr;
    std::size_t _originalSize = 0;
};

TEST_F(ItemInventoryTest, InvalidExplicitSlotIsRejected)
{
    EXPECT_FALSE(_player->IsValidPos(INVENTORY_SLOT_BAG_0, NULL_SLOT, true));
    EXPECT_FALSE(_player->IsValidPos(INVENTORY_SLOT_BAG_START, 0, true));
    EXPECT_EQ(_player->GetItemCount(1), 0u);
}

TEST_F(ItemInventoryTest, InsufficientSpaceDoesNotCreateItems)
{
    ItemPosCountVec destination;
    uint32 noSpace = 0;
    EXPECT_EQ(_player->CanStoreItem(NULL_BAG, NULL_SLOT, destination, 1, 17, nullptr, false, &noSpace),
        EQUIP_ERR_INVENTORY_FULL);
    EXPECT_EQ(noSpace, 1u);
    EXPECT_EQ(_player->GetItemCount(1), 0u);
}

TEST_F(ItemInventoryTest, UnusableItemIsRejected)
{
    _template.RequiredLevel = 2;
    EXPECT_EQ(_player->CanUseItem(&_template), EQUIP_ERR_CANT_EQUIP_LEVEL_I);
    EXPECT_EQ(_player->GetItemCount(1), 0u);
}

TEST_F(ItemInventoryTest, ForeignBoundItemCannotBeUsedOrStored)
{
    TestPlayer* owner = CreateTestPlayer(2);
    Item item;
    ASSERT_TRUE(item.Create(10, 1, owner));
    item.SetBinding(true);
    EXPECT_EQ(_player->CanUseItem(&item), EQUIP_ERR_DONT_OWN_THAT_ITEM);
    ItemPosCountVec destination;
    EXPECT_EQ(_player->CanStoreItem(NULL_BAG, NULL_SLOT, destination, 1, 1, &item, false),
        EQUIP_ERR_DONT_OWN_THAT_ITEM);
    EXPECT_TRUE(destination.empty());
    EXPECT_EQ(item.GetOwnerGUID(), owner->GetGUID());
    EXPECT_EQ(item.GetCount(), 1u);
    EXPECT_EQ(_player->GetItemCount(1), 0u);
}
