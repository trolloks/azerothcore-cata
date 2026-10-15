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

#include "mpq_libmpq04.h"
#include "StringFormat.h"
#include <cstdio>
#include <deque>
#include <string>

ArchiveSet gOpenArchives;

// These extractor tools never start the server logger (LOG_* needs sLog->Initialize()), so the
// diagnostics below go to stdout through the project string helper instead of printf specifiers.
static void PrintMessage(std::string const& message)
{
    fputs(message.c_str(), stdout);
    fputc('\n', stdout);
}

MPQArchive::MPQArchive(char const* filename):
    mpq_a(nullptr)
{
    PrintMessage(Acore::StringFormat("Opening {}", filename));
    if (!SFileOpenArchive(filename, 0, MPQ_OPEN_READ_ONLY, &mpq_a))
    {
        PrintMessage(Acore::StringFormat("Error opening archive '{}': error {}", filename, uint32(SErrGetLastError())));
        mpq_a = nullptr;
        return;
    }
    gOpenArchives.push_front(this);
}

bool MPQArchive::ApplyPatch(char const* filename)
{
    if (!mpq_a)
        return false;

    PrintMessage(Acore::StringFormat("Applying patch {}", filename));
    if (!SFileOpenPatchArchive(mpq_a, filename, "", 0))
    {
        PrintMessage(Acore::StringFormat("Error applying patch '{}': error {}", filename, uint32(SErrGetLastError())));
        return false;
    }

    return true;
}

void MPQArchive::close()
{
    if (mpq_a)
        SFileCloseArchive(mpq_a);
    mpq_a = nullptr;
}

void MPQArchive::GetFileListTo(vector<string>& filelist)
{
    if (!mpq_a)
        return;

    SFILE_FIND_DATA data;
    HANDLE hFind = SFileFindFirstFile(mpq_a, "*", &data, nullptr);
    if (!hFind)
        return;

    do
        filelist.emplace_back(data.cFileName);
    while (SFileFindNextFile(hFind, &data));

    SFileFindClose(hFind);
}

MPQFile::MPQFile(char const* filename):
    eof(false),
    buffer(nullptr),
    pointer(0),
    size(0)
{
    for (auto & gOpenArchive : gOpenArchives)
    {
        HANDLE hFile;
        if (!SFileOpenFileEx(gOpenArchive->mpq_a, filename, SFILE_OPEN_FROM_MPQ, &hFile))
            continue;

        DWORD fileSize = SFileGetFileSize(hFile, nullptr);

        // HACK: in patch.mpq some files don't want to open and give 1 for filesize
        if (fileSize == SFILE_INVALID_SIZE || fileSize <= 1)
        {
            SFileCloseFile(hFile);
            eof = true;
            buffer = nullptr;
            return;
        }

        size = fileSize;
        buffer = new char[size];

        DWORD transferred = 0;
        if (!SFileReadFile(hFile, buffer, static_cast<DWORD>(size), &transferred, nullptr) || transferred != size)
        {
            PrintMessage(Acore::StringFormat("Error reading '{}': expected {} bytes, read {}",
                filename, uint32(size), uint32(transferred)));
            SFileCloseFile(hFile);
            delete[] buffer;
            buffer = nullptr;
            size = 0;
            eof = true;
            return;
        }
        SFileCloseFile(hFile);
        return;
    }
    eof = true;
    buffer = nullptr;
}

std::size_t MPQFile::read(void* dest, std::size_t bytes)
{
    if (eof) return 0;

    std::size_t rpos = pointer + bytes;
    if (rpos > size)
    {
        bytes = size - pointer;
        eof = true;
    }

    memcpy(dest, &(buffer[pointer]), bytes);

    pointer = rpos;

    return bytes;
}

void MPQFile::seek(int offset)
{
    pointer = offset;
    eof = (pointer >= size);
}

void MPQFile::seekRelative(int offset)
{
    pointer += offset;
    eof = (pointer >= size);
}

void MPQFile::close()
{
    delete[] buffer;
    buffer = nullptr;
    eof = true;
}
