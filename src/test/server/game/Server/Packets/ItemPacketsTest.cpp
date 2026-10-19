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
#include "LootMgr.h"
#include "ObjectMgr.h"
#include "ScriptDefines/AchievementScript.h"
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

TEST(ItemPacketsTest, BuyItemReadsCataItemTypeAndDestinationBag)
{
    EXPECT_EQ(uint16(CMSG_BUY_ITEM), 0x0736);
    WorldPacket raw(CMSG_BUY_ITEM, 30);
    raw << uint64(10);                              // vendorguid: Cata client packets carry it unpacked
    raw << uint8(ITEM_VENDOR_TYPE_ITEM);
    raw << uint32(0x0000ABCD);
    raw << uint32(3);
    raw << uint32(2);
    raw << uint64(0);                               // empty destination bag guid
    raw << uint8(42);                               // destination bag slot
    WorldPackets::Item::BuyItem packet(std::move(raw));
    packet.Read();
    EXPECT_EQ(packet.VendorGuid.GetCounter(), 10u);
    EXPECT_EQ(packet.ItemType, uint8(ITEM_VENDOR_TYPE_ITEM));
    EXPECT_EQ(packet.Item, 0x0000ABCDu);
    EXPECT_EQ(packet.Slot, 3u);
    EXPECT_EQ(packet.Count, 2u);
    EXPECT_TRUE(packet.BagGuid.IsEmpty());
    EXPECT_EQ(packet.BagSlot, 42);
    EXPECT_EQ(packet.GetRawPacket()->rpos(), packet.GetSize());
}

TEST(ItemPacketsTest, BuyItemRejectsTruncatedCataDestinationBag)
{
    WorldPacket raw(CMSG_BUY_ITEM, 20);
    raw << uint64(10);
    raw << uint8(ITEM_VENDOR_TYPE_ITEM);
    raw << uint32(0x0000ABCD);
    raw << uint32(3);
    raw << uint32(2);
    WorldPackets::Item::BuyItem packet(std::move(raw));
    EXPECT_THROW(packet.Read(), ByteBufferException);
}

class ItemInventoryTest : public IntegrationTestFixture
{
protected:
    void SetUp() override
    {
        IntegrationTestFixture::SetUp();
        ScriptRegistry<AchievementScript>::InitEnabledHooksIfNeeded(ACHIEVEMENTHOOK_END);
        _templates = const_cast<std::vector<ItemTemplate*>*>(sObjectMgr->GetItemTemplateStoreFast());
        _originalSize = _templates->size();
        if (_templates->size() <= 1)
            _templates->resize(2);
        _originalTemplate = (*_templates)[1];
        _template.ItemId = 1;
        _template.Class = ITEM_CLASS_MISC;
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

TEST(LootViewTest, NoPermissionIncludesBothCounts)
{
    Loot loot(123);
    ByteBuffer response;
    response << LootView(loot, nullptr, NONE_PERMISSION);
    ASSERT_EQ(response.size(), 6u);
    EXPECT_EQ(response.read<uint32>(), 0u);
    EXPECT_EQ(response.read<uint8>(), 0);
    EXPECT_EQ(response.read<uint8>(), 0);
}

TEST_F(ItemInventoryTest, EmptyLootViewIncludesCurrencyCount)
{
    Loot loot(123);
    ByteBuffer response;
    response << LootView(loot, _player);
    ASSERT_EQ(response.size(), 6u);
    EXPECT_EQ(response.read<uint32>(), 123u);
    EXPECT_EQ(response.read<uint8>(), 0);
    EXPECT_EQ(response.read<uint8>(), 0);
}

TEST_F(ItemInventoryTest, LootItemFollowsBothCounts)
{
    _template.DisplayInfoID = 123;
    Loot loot(42);
    LootItem item{};
    item.itemid = 1;
    item.count = 3;
    loot.items.push_back(item);
    ByteBuffer response;
    response << LootView(loot, _player);
    ASSERT_EQ(response.size(), 28u);
    EXPECT_EQ(response.read<uint32>(), 42u);
    EXPECT_EQ(response.read<uint8>(), 1);
    EXPECT_EQ(response.read<uint8>(), 0);
    EXPECT_EQ(response.read<uint8>(), 0);
    EXPECT_EQ(response.read<uint32>(), 1u);
    EXPECT_EQ(response.read<uint32>(), 3u);
    EXPECT_EQ(response.read<uint32>(), 123u);
    EXPECT_EQ(response.read<uint32>(), 0u);
    EXPECT_EQ(response.read<int32>(), 0);
    EXPECT_EQ(response.read<uint8>(), LOOT_SLOT_TYPE_ALLOW_LOOT);
    EXPECT_EQ(response.rpos(), response.size());
}

TEST_F(ItemInventoryTest, FullInventoryPreservesLoot)
{
    ASSERT_TRUE(_player->AddItem(1, 16));
    Loot loot;
    LootItem item{};
    item.itemid = 1;
    item.count = 1;
    loot.items.push_back(item);
    loot.unlootedCount = 1;
    InventoryResult result;
    ASSERT_EQ(_player->StoreLootItem(0, &loot, result), &loot.items[0]);
    EXPECT_EQ(result, EQUIP_ERR_INVENTORY_FULL);
    EXPECT_FALSE(loot.items[0].is_looted);
    EXPECT_EQ(loot.items[0].count, 1u);
    EXPECT_EQ(loot.unlootedCount, 1);
    EXPECT_EQ(_player->GetItemCount(1), 16u);
}

TEST_F(ItemInventoryTest, ForeignLootWinnerCannotTakeItem)
{
    Loot loot;
    LootItem item{};
    item.itemid = 1;
    item.count = 1;
    item.rollWinnerGUID = CreateTestPlayer(2)->GetGUID();
    loot.items.push_back(item);
    loot.unlootedCount = 1;
    InventoryResult result;
    EXPECT_EQ(_player->StoreLootItem(0, &loot, result), nullptr);
    EXPECT_FALSE(loot.items[0].is_looted);
    EXPECT_EQ(loot.unlootedCount, 1);
    EXPECT_EQ(_player->GetItemCount(1), 0u);
}

TEST_F(ItemInventoryTest, ForeignContainerCannotLoseItemsOrMoney)
{
    TestPlayer* owner = CreateTestPlayer(2);
    Item* container = new Item();
    ASSERT_TRUE(container->Create(50, 1, owner));
    ItemPosCountVec destination{{uint16((INVENTORY_SLOT_BAG_0 << 8) | INVENTORY_SLOT_ITEM_START), 1}};
    owner->StoreItem(destination, container, false);
    LootItem item{};
    item.itemid = 1;
    item.count = 1;
    container->loot.items.push_back(item);
    container->loot.unlootedCount = 1;
    container->loot.gold = 37;
    _player->SetLootGUID(container->GetGUID());

    WorldPacket take(CMSG_AUTOSTORE_LOOT_ITEM, 1);
    take << uint8(0);
    _player->GetSession()->HandleAutostoreLootItemOpcode(take);
    WorldPacket money(CMSG_LOOT_MONEY, 0);
    _player->GetSession()->HandleLootMoneyOpcode(money);
    EXPECT_FALSE(container->loot.items[0].is_looted);
    EXPECT_EQ(container->loot.unlootedCount, 1);
    EXPECT_EQ(container->loot.gold, 37u);
    EXPECT_EQ(owner->GetItemCount(1), 1u);
    EXPECT_EQ(_player->GetItemCount(1), 0u);
    EXPECT_EQ(_player->GetMoney(), 0u);
}

TEST_F(ItemInventoryTest, InvalidAndRepeatedLootSlotsPreserveItems)
{
    Loot loot;
    LootItem item{};
    item.itemid = 1;
    item.count = 1;
    loot.items.push_back(item);
    loot.unlootedCount = 1;
    InventoryResult result;
    EXPECT_EQ(_player->StoreLootItem(255, &loot, result), nullptr);
    EXPECT_FALSE(loot.items[0].is_looted);
    EXPECT_EQ(loot.unlootedCount, 1);
    ASSERT_NE(_player->StoreLootItem(0, &loot, result), nullptr);
    ASSERT_EQ(result, EQUIP_ERR_OK);
    EXPECT_EQ(_player->GetItemCount(1), 1u);
    EXPECT_EQ(loot.unlootedCount, 0);
    EXPECT_EQ(_player->StoreLootItem(0, &loot, result), nullptr);
    EXPECT_EQ(_player->StoreLootItem(255, &loot, result), nullptr);
    EXPECT_EQ(_player->GetItemCount(1), 1u);
    EXPECT_EQ(loot.unlootedCount, 0);
}

TEST_F(ItemInventoryTest, ForeignAndRepeatedReleaseCannotChangeLootState)
{
    ObjectGuid source = ObjectGuid::Create<HighGuid::GameObject>(2843, 10);
    _player->SetLootGUID(source);
    WorldPacket foreignRelease(CMSG_LOOT_RELEASE, 8);
    foreignRelease << ObjectGuid::Create<HighGuid::GameObject>(2843, 11);
    _player->GetSession()->HandleLootReleaseOpcode(foreignRelease);
    EXPECT_EQ(_player->GetLootGUID(), source);
    WorldPacket release(CMSG_LOOT_RELEASE, 8);
    release << source;
    _player->GetSession()->HandleLootReleaseOpcode(release);
    EXPECT_TRUE(_player->GetLootGUID().IsEmpty());
    release.rpos(0);
    _player->GetSession()->HandleLootReleaseOpcode(release);
    EXPECT_TRUE(_player->GetLootGUID().IsEmpty());
    EXPECT_EQ(_player->GetItemCount(1), 0u);
    EXPECT_EQ(_player->GetMoney(), 0u);
}

TEST(LootViewTest, CataNotificationOpcodes)
{
    EXPECT_EQ(uint16(SMSG_LOOT_RELEASE_RESPONSE), 0x6D25);
    EXPECT_EQ(uint16(SMSG_LOOT_CLEAR_MONEY), 0x2B37);
    EXPECT_EQ(uint16(SMSG_LOOT_RESPONSE), 0x4C16);
    EXPECT_EQ(uint16(CMSG_AUTOSTORE_LOOT_ITEM), 0x0E34);
}
