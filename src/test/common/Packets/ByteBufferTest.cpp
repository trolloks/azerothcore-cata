/*
 * This file is part of the AzerothCore Project. See AUTHORS file for Copyright information
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program. If not, see <http://www.gnu.org/licenses/>.
 */

#include "ByteBuffer.h"
#include "MessageBuffer.h"
#include "gtest/gtest.h"

#include <initializer_list>

namespace
{
void ExpectBytes(ByteBuffer const& buffer, std::initializer_list<uint8> expected)
{
    ASSERT_EQ(buffer.size(), expected.size());

    std::size_t index = 0;
    for (uint8 value : expected)
        EXPECT_EQ(buffer[index++], value);
}

ByteBuffer BufferWithBytes(std::initializer_list<uint8> bytes)
{
    ByteBuffer buffer(bytes.size());
    for (uint8 value : bytes)
        buffer << value;

    return buffer;
}
}

TEST(ByteBufferBitsTest, WritesCataclysmAuthResponseBits)
{
    ByteBuffer buffer;
    buffer.WriteBit(true);
    buffer.WriteBit(false);
    buffer.WriteBit(true);
    buffer.FlushBits();

    ExpectBytes(buffer, { 0xA0 });
}

TEST(ByteBufferBitsTest, WritesCataclysmCharacterCountBits)
{
    ByteBuffer buffer;
    buffer.WriteBits(0, 23);
    buffer.WriteBit(true);
    buffer.WriteBits(1, 17);
    buffer.FlushBits();

    ExpectBytes(buffer, { 0x00, 0x00, 0x01, 0x00, 0x00, 0x80 });
}

TEST(ByteBufferBitsTest, WritesExactBytesForSupportedWidths)
{
    ByteBuffer zeroBits;
    zeroBits.WriteBits(0xFF, 0);
    zeroBits.FlushBits();
    EXPECT_TRUE(zeroBits.empty());

    ByteBuffer oneBit;
    oneBit.WriteBits(1, 1);
    oneBit.FlushBits();
    ExpectBytes(oneBit, { 0x80 });

    ByteBuffer sevenBits;
    sevenBits.WriteBits(0x55, 7);
    sevenBits.FlushBits();
    ExpectBytes(sevenBits, { 0xAA });

    ByteBuffer eightBits;
    eightBits.WriteBits(0xA5, 8);
    eightBits.FlushBits();
    ExpectBytes(eightBits, { 0xA5 });

    ByteBuffer nineBits;
    nineBits.WriteBits(0x101, 9);
    nineBits.FlushBits();
    ExpectBytes(nineBits, { 0x80, 0x80 });

    ByteBuffer thirtyTwoBits;
    thirtyTwoBits.WriteBits(0x89ABCDEF, 32);
    thirtyTwoBits.FlushBits();
    ExpectBytes(thirtyTwoBits, { 0x89, 0xAB, 0xCD, 0xEF });

    ByteBuffer sixtyFourBits;
    sixtyFourBits.WriteBits(UI64LIT(0x0123456789ABCDEF), 64);
    sixtyFourBits.FlushBits();
    ExpectBytes(sixtyFourBits, { 0x01, 0x23, 0x45, 0x67, 0x89, 0xAB, 0xCD, 0xEF });
}

TEST(ByteBufferBitsTest, ReadsExactValuesForSupportedWidths)
{
    ByteBuffer zeroBits;
    EXPECT_EQ(zeroBits.ReadBits(0), 0u);

    ByteBuffer oneBit = BufferWithBytes({ 0x80 });
    EXPECT_EQ(oneBit.ReadBits(1), 1u);

    ByteBuffer sevenBits = BufferWithBytes({ 0xAA });
    EXPECT_EQ(sevenBits.ReadBits(7), 0x55u);

    ByteBuffer eightBits = BufferWithBytes({ 0xA5 });
    EXPECT_EQ(eightBits.ReadBits(8), 0xA5u);

    ByteBuffer nineBits = BufferWithBytes({ 0x80, 0x80 });
    EXPECT_EQ(nineBits.ReadBits(9), 0x101u);

    ByteBuffer thirtyTwoBits = BufferWithBytes({ 0x89, 0xAB, 0xCD, 0xEF });
    EXPECT_EQ(thirtyTwoBits.ReadBits(32), 0x89ABCDEFu);
}

TEST(ByteBufferBitsTest, PreservesPartialBitsAcrossCopies)
{
    ByteBuffer source;
    source.WriteBits(0x5, 3);

    ByteBuffer constructed(source);
    constructed.FlushBits();
    ExpectBytes(constructed, { 0xA0 });

    ByteBuffer assigned;
    assigned = source;
    assigned.FlushBits();
    ExpectBytes(assigned, { 0xA0 });
}

TEST(ByteBufferBitsTest, PreservesPartialBitsAcrossMovesAndClearsSources)
{
    ByteBuffer constructorSource;
    constructorSource.WriteBits(0x5, 3);
    ByteBuffer constructed(std::move(constructorSource));
    constructed.FlushBits();
    ExpectBytes(constructed, { 0xA0 });

    constructorSource.WriteBit(false);
    constructorSource.FlushBits();
    ExpectBytes(constructorSource, { 0x00 });

    ByteBuffer assignmentSource;
    assignmentSource.WriteBits(0x5, 3);
    ByteBuffer assigned;
    assigned.WriteBits(0, 2);
    assigned = std::move(assignmentSource);
    assigned.FlushBits();
    ExpectBytes(assigned, { 0xA0 });

    assignmentSource.WriteBit(false);
    assignmentSource.FlushBits();
    ExpectBytes(assignmentSource, { 0x00 });
}

TEST(ByteBufferBitsTest, ClearAndConstructorsUseEmptyBitState)
{
    ByteBuffer buffer;
    buffer.WriteBit(true);
    buffer.clear();
    buffer.WriteBit(false);
    buffer.FlushBits();
    ExpectBytes(buffer, { 0x00 });

    ByteBuffer reserved(16);
    reserved.WriteBit(false);
    reserved.FlushBits();
    ExpectBytes(reserved, { 0x00 });

    MessageBuffer message(0);
    ByteBuffer messageBacked(std::move(message));
    messageBacked.WriteBit(false);
    messageBacked.FlushBits();
    ExpectBytes(messageBacked, { 0x00 });
}

TEST(ByteBufferBitsTest, ResizeResetsBitState)
{
    ByteBuffer buffer;
    buffer.WriteBit(true);
    buffer.resize(0);
    buffer.WriteBit(false);
    buffer.FlushBits();

    ExpectBytes(buffer, { 0x00 });
}

TEST(ByteBufferBitsTest, RejectsUnsupportedWidths)
{
    ByteBuffer buffer;
    EXPECT_THROW(buffer.WriteBits(0, 65), ByteBufferInvalidValueException);
    EXPECT_THROW(buffer.ReadBits(33), ByteBufferInvalidValueException);
}

TEST(ByteBufferBitsTest, PackedGuidByteSequenceMatchesCataclysm)
{
    ByteBuffer encoded;
    encoded.WriteByteSeq(0x00);
    encoded.WriteByteSeq(0x12);
    encoded.WriteByteSeq(0xFF);
    ExpectBytes(encoded, { 0x13, 0xFE });

    ByteBuffer decoded = BufferWithBytes({ 0x13, 0xFE });
    uint8 first = 0x01;
    uint8 second = 0x01;
    decoded.ReadByteSeq(first);
    decoded.ReadByteSeq(second);
    EXPECT_EQ(first, 0x12);
    EXPECT_EQ(second, 0xFF);
}
