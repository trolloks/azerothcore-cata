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

#ifndef ACORE_DBCSFRM_H
#define ACORE_DBCSFRM_H

char constexpr Achievementfmt[] = "niixSxiixixxii";
char constexpr AchievementCategoryfmt[] = "nixx";
char constexpr AchievementCriteriafmt[] = "niiiqixxxxiixixxxiiiiii";
char constexpr AreaTableEntryfmt[] = "niiiixxxxxiSiiiiixxxxxxxxx";
char constexpr AreaGroupEntryfmt[] = "niiiiiii";
char constexpr AreaPOIEntryfmt[] = "niiiiiiiiiiifffixixxix";
char constexpr AuctionHouseEntryfmt[] = "niiix";
char constexpr BankBagSlotPricesEntryfmt[] = "ni";
char constexpr BarberShopStyleEntryfmt[] = "nixxxiii";
char constexpr BattlemasterListEntryfmt[] = "niiiiiiiiixSiixxxxxx";
char constexpr CharStartOutfitEntryfmt[] = "dbbbXiiiiiiiiiiiiiiiiiiiiiiiixxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxii";
char constexpr CharSectionsEntryfmt[] = "diiixxxiii";
char constexpr CharTitlesEntryfmt[] = "nxSSix";
char constexpr ChatChannelsEntryfmt[] = "nixSx"; // ChatChannelsEntryfmt, index not used (more compact store)
char constexpr ChrClassesEntryfmt[] = "nixSxxxixiixxx";
char constexpr ChrRacesEntryfmt[] = "niixiixixxxxiiSxxxxxixxx";
char constexpr CinematicCameraEntryfmt[] = "nsiffff";
char constexpr CinematicSequencesEntryfmt[] = "nxixxxxxxx";
char constexpr CreatureDisplayInfofmt[] = "nixifxxxxxxxxxxxx";
char constexpr CreatureDisplayInfoExtrafmt[] = "dixxxxxxxxxxxxxxxxxxx";
char constexpr CreatureFamilyfmt[] = "nfifiiiiixSx";
char constexpr CreatureModelDatafmt[] = "nixxfxxxxxxxxxxffxxxxxxxxxxxxxf";
char constexpr CreatureSpellDatafmt[] = "niiiixxxx";
char constexpr CreatureTypefmt[] = "nxx";
char constexpr CurrencyTypesfmt[] = "xnxixxxxxxx";
char constexpr DestructibleModelDatafmt[] = "nxxixxxixxxixxxixxxxxxxx";
char constexpr DungeonEncounterfmt[] = "niixiSxx";
char constexpr DurabilityCostsfmt[] = "niiiiiiiiiiiiiiiiiiiiiiiiiiiii";
char constexpr DurabilityQualityfmt[] = "nf";
char constexpr EmotesEntryfmt[] = "nxxiiixx";
char constexpr EmotesTextEntryfmt[] = "nxixxxxxxxxxxxxxxxx";
char constexpr EmotesTextSoundEntryfmt[] = "niiii";
char constexpr FactionEntryfmt[] = "niiiiiiiiiiiiiiiiiiffixSxx";
char constexpr FactionTemplateEntryfmt[] = "niiiiiiiiiiiii";
char constexpr GameObjectArtKitfmt[] = "nxxxxxxx";
char constexpr GameObjectDisplayInfofmt[] = "nsxxxxxxxxxxffffffxxx";
char constexpr GemPropertiesEntryfmt[] = "nixxix";
char constexpr GlyphPropertiesfmt[] = "niix";
char constexpr GlyphSlotfmt[] = "nii";
char constexpr GtBarberShopCostBasefmt[] = "df";
char constexpr GtCombatRatingsfmt[] = "df";
char constexpr GtChanceToMeleeCritBasefmt[] = "df";
char constexpr GtChanceToMeleeCritfmt[] = "df";
char constexpr GtChanceToSpellCritBasefmt[] = "df";
char constexpr GtChanceToSpellCritfmt[] = "df";
char constexpr GtNPCManaCostScalerfmt[] = "df";
char constexpr GtOCTClassCombatRatingScalarfmt[] = "df";
char constexpr GtOCTRegenHPfmt[] = "df";
//char constexpr GtOCTRegenMPfmt[] = "f";
char constexpr GtRegenHPPerSptfmt[] = "df";
char constexpr GtRegenMPPerSptfmt[] = "df";
char constexpr Holidaysfmt[] = "niiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiixxsiix";
char constexpr Itemfmt[] = "niiiiiii";
char constexpr Phasefmt[] = "nsi";
char constexpr PhaseGroupfmt[] = "nii";
char constexpr ItemBagFamilyfmt[] = "nx";
char constexpr ItemDisplayTemplateEntryfmt[] = "nxxxxsxxxxxxxxxxxxxxxxxxx";
//char constexpr ItemCondExtCostsEntryfmt[] = "xiii";
char constexpr ItemExtendedCostEntryfmt[] = "niiiiiiiiiiiiiiiiiiiiiiiiiiiiii";
char constexpr ItemLimitCategoryEntryfmt[] = "nxii";
char constexpr ItemRandomPropertiesfmt[] = "nxiiiiiS";
char constexpr ItemRandomSuffixfmt[] = "nSxiiiiiiiiii";
char constexpr ItemSetEntryfmt[] = "dSiiiiiiiiiixxxxxxxiiiiiiiiiiiiiiiiii";
char constexpr LFGDungeonEntryfmt[] = "nSiiiiiiiiixxixixxxxx";
char constexpr LightEntryfmt[] = "nifffxxxxxxxxxx";
char constexpr LiquidTypefmt[] = "nxxixixxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx";
char constexpr LockEntryfmt[] = "niiiiiiiiiiiiiiiiiiiiiiiixxxxxxxx";
char constexpr MailTemplateEntryfmt[] = "nxS";
char constexpr MapEntryfmt[] = "nxiixxSiixxxiffxixix";
char constexpr MapDifficultyEntryfmt[] = "diisiix";
char constexpr MovieEntryfmt[] = "nxxx";
char constexpr NamesReservedfmt[] = "xsx";
char constexpr NamesProfanityfmt[] = "xsx";
char constexpr OverrideSpellDatafmt[] = "niiiiiiiiiixx";
char constexpr PowerDisplayfmt[] = "nixXXX";
char constexpr QuestSortEntryfmt[] = "nx";
char constexpr QuestXPfmt[] = "niiiiiiiiii";
char constexpr QuestFactionRewardfmt[] = "niiiiiiiiii";
char constexpr PvPDifficultyfmt[] = "diiiii";
char constexpr RandomPropertiesPointsfmt[] = "niiiiiiiiiiiiiii";
char constexpr ScalingStatDistributionfmt[] = "niiiiiiiiiiiiiiiiiiiiix";
char constexpr ScalingStatValuesfmt[] = "niiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiii";
char constexpr SkillLinefmt[] = "niSxixi";
char constexpr SkillLineAbilityfmt[] = "niiiixxiiiiiii";
char constexpr SkillRaceClassInfofmt[] = "diiiiiiix";
char constexpr SkillTiersfmt[] = "nxxxxxxxxxxxxxxxxiiiiiiiiiiiiiiii";
char constexpr SoundEntriesfmt[] = "nxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx";
char constexpr SpellCastTimefmt[] = "nixx";
char constexpr SpellCategoryfmt[] = "nixx";
char constexpr SpellDifficultyfmt[] = "niiii";
char constexpr SpellDurationfmt[] = "niii";
char constexpr SpellEntryfmt[] = "niiiiiiiiiiiiiiifiiiiSSxxiixxifiiiiiiixiiiiiiiii";
char constexpr SpellEffectEntryfmt[] = "nifiiiffiiiiiifiifiiiiiiiix";
char constexpr SpellAuraOptionsEntryfmt[] = "niiii";
char constexpr SpellAuraRestrictionsEntryfmt[] = "diiiiiiii";
char constexpr SpellCastingRequirementsEntryfmt[] = "niiiiii";
char constexpr SpellCategoriesEntryfmt[] = "diiiiii";
char constexpr SpellClassOptionsEntryfmt[] = "dxiiiix";
char constexpr SpellCooldownsEntryfmt[] = "diii";
char constexpr SpellEquippedItemsEntryfmt[] = "diii";
char constexpr SpellInterruptsEntryfmt[] = "diiiii";
char constexpr SpellLevelsEntryfmt[] = "diii";
char constexpr SpellPowerEntryfmt[] = "diiiixxf";
char constexpr SpellReagentsEntryfmt[] = "diiiiiiiiiiiiiiii";
char constexpr SpellScalingEntryfmt[] = "diiiiffffffffffi";
char constexpr SpellShapeshiftEntryfmt[] = "niiiix";
char constexpr SpellTargetRestrictionsEntryfmt[] = "nfiiii";
char constexpr SpellTotemsEntryfmt[] = "niiii";
char constexpr SpellFocusObjectfmt[] = "nx";
char constexpr SpellItemEnchantmentfmt[] = "niiiiiiixxxiiiSiiiiiiix";
char constexpr SpellItemEnchantmentConditionfmt[] = "nbbbXxxxxxxbbbXXbbbxiiixxXXXXXX";
char constexpr SpellRadiusfmt[] = "nfff";
char constexpr SpellRangefmt[] = "nffffixx";
char constexpr SpellRuneCostfmt[] = "niiii";
char constexpr SpellShapeshiftFormEntryfmt[] = "nxxiixiiixxiiiiiiiixx";
char constexpr SpellVisualfmt[] = "dxxxxxxiixxxxxxxxxxxxxxxxxxxxxxxx";
char constexpr StableSlotPricesfmt[] = "ni";
char constexpr SummonPropertiesfmt[] = "niiiii";
char constexpr TalentEntryfmt[] = "niiiiiiiiixxixxixxx";
char constexpr TalentTabEntryfmt[] = "nxxiiixxxxx";
char constexpr TaxiNodesEntryfmt[] = "nifffSxxxii";
char constexpr TaxiPathEntryfmt[] = "niii";
char constexpr TaxiPathNodeEntryfmt[] = "diiifffiiii";
char constexpr TeamContributionPointsfmt[] = "df";
char constexpr TotemCategoryEntryfmt[] = "nxii";
char constexpr TransportAnimationfmt[] = "diifffx";
char constexpr TransportRotationfmt[] = "diiffff";
char constexpr VehicleEntryfmt[] = "niffffiiiiiiiifffffffffffffffssssfifiixx";
char constexpr VehicleSeatEntryfmt[] = "niiffffffffffiiiiiifffffffiiifffiiiiiiiffiiiiixxxxxxxxxxxxxxxxxxxx";
char constexpr WMOAreaTableEntryfmt[] = "niiixxxxxiixxxx";
char constexpr WorldMapAreaEntryfmt[] = "xinxffffixx";
char constexpr WorldMapOverlayEntryfmt[] = "nxiiiixxxxxxxxx";

#endif
