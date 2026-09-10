/*
 * This file is part of the AzerothCore Project. See AUTHORS file for Copyright information
 * Licensed under the GNU General Public License, version 2 or later.
 */

#include "DBCFileLoader.h"
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
