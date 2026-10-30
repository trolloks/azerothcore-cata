/*
 * This file is part of the AzerothCore Project. See AUTHORS file for Copyright information
 * Licensed under the GNU General Public License, version 2 or later.
 */

#include "DBCFileLoader.h"
#include "DBCStore.h"
#include "DBCStructure.h"
#include "DBCfmt.h"
#include <gtest/gtest.h>
#include <array>
#include <chrono>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <memory>
#include <vector>

TEST(DBCFileLoaderTest, NativeStringsPreserveAdjacentFieldsAndRejectLegacyLayout)
{
    std::vector<char> bytes;
    auto word = [&bytes](uint32 value)
    {
        for (uint8 shift = 0; shift < 32; shift += 8)
            bytes.push_back(static_cast<char>(value >> shift));
    };
    word(0x43424457); // WDBC
    word(1); // records
    word(3); // fields
    word(12); // record width
    word(6); // string block width
    word(7); // ID
    word(1); // name offset
    word(42); // adjacent numeric field
    for (char value : std::array<char, 6>{0, 'T', 'e', 's', 't', 0})
        bytes.push_back(value);

    auto path = std::filesystem::temp_directory_path() / ("cata-dbc-"
        + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) + ".dbc");
    {
        std::ofstream file(path, std::ios::binary);
        file.write(bytes.data(), bytes.size());
    }
    DBCFileLoader wrongLayout;
    EXPECT_FALSE(wrongLayout.Load(path.string().c_str(), "ni"));
    DBCFileLoader loader;
    bool loaded = loader.Load(path.string().c_str(), "nSi");
    std::filesystem::remove(path);
    ASSERT_TRUE(loaded);

    uint32 count = 0;
    char** index = nullptr;
    std::unique_ptr<char[]> records(loader.AutoProduceData("nSi", count, index));
    std::unique_ptr<char*[]> entries(index);
    std::unique_ptr<char[]> strings(loader.AutoProduceStrings("nSi", records.get()));
    ASSERT_EQ(count, 8u);
    ASSERT_NE(entries[7], nullptr);
    ASSERT_NE(strings, nullptr);
    EXPECT_EQ(DBCFileLoader::GetFormatRecordSize("nSi"), 8u + DBC_LOCALE_SLOTS * sizeof(char*));
    for (uint32 locale = 0; locale < DBC_LOCALE_SLOTS; ++locale)
    {
        char const* name = nullptr;
        std::memcpy(&name, entries[7] + 4 + locale * sizeof(char*), sizeof(name));
        EXPECT_STREQ(name, "Test");
    }
    uint32 value = 0;
    std::memcpy(&value, entries[7] + 4 + DBC_LOCALE_SLOTS * sizeof(char*), sizeof(value));
    EXPECT_EQ(value, 42u);
}

TEST(DBCFileLoaderTest, CurrencyTypesFormatTracksStructLayout)
{
    // The loader packs only the non-skipped fields, in format order, and keys the store on the
    // first index field - the 4.3.4 currency id at field 0, not the category at field 1.
    int32 indexPosition = -1;
    EXPECT_EQ(DBCFileLoader::GetFormatRecordSize(CurrencyTypesfmt, &indexPosition),
              sizeof(CurrencyTypesEntry));
    EXPECT_EQ(indexPosition, 0);

    CurrencyTypesEntry entry{};
    entry.Flags = 0x008; // Uses1To100ScalarForDisplay (Justice Points)
    EXPECT_TRUE(entry.UsesPrecision());
    entry.Flags = 0; // Darkmoon Prize Ticket
    EXPECT_FALSE(entry.UsesPrecision());
}

TEST(DBCFileLoaderTest, CurrencyTypesLookupUsesCurrencyIdAndReadsFlags)
{
    std::vector<char> bytes;
    auto word = [&bytes](uint32 value)
    {
        for (uint8 shift = 0; shift < 32; shift += 8)
            bytes.push_back(static_cast<char>(value >> shift));
    };
    word(0x43424457); // WDBC
    word(2); // records
    word(11); // fields
    word(44); // record width
    word(0); // string block width
    uint32 const records[2][11] =
    {
        {395, 22, 0, 0, 0, 0, 0, 400000, 0, 0x008, 0}, // Justice Points (4000 displayed)
        {515, 1, 0, 0, 0, 0, 0, 0, 0, 0x000, 0}, // Darkmoon Prize Ticket
    };
    for (auto const& record : records)
        for (uint32 value : record)
            word(value);

    auto path = std::filesystem::temp_directory_path() / ("cata-currency-"
        + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) + ".dbc");
    {
        std::ofstream file(path, std::ios::binary);
        file.write(bytes.data(), bytes.size());
    }
    DBCStorage<CurrencyTypesEntry> store(CurrencyTypesfmt);
    bool loaded = store.Load(path.string().c_str());
    std::filesystem::remove(path);
    ASSERT_TRUE(loaded);

    CurrencyTypesEntry const* justice = store.LookupEntry(395);
    ASSERT_NE(justice, nullptr);
    EXPECT_TRUE(justice->UsesPrecision());
    EXPECT_EQ(justice->MaxQty, 400000u);
    CurrencyTypesEntry const* ticket = store.LookupEntry(515);
    ASSERT_NE(ticket, nullptr);
    EXPECT_FALSE(ticket->UsesPrecision());
    EXPECT_EQ(ticket->MaxQty, 0u);

    EXPECT_EQ(store.LookupEntry(22), nullptr); // category must not be the key
}
