-- Native Cataclysm server-only spell required by Human Warrior Defensive State (5301).
-- Source: TC 4bf9a6f4a4 (Kaelima), TDB 434.22011; unchanged at c699217775d90794158422387b07a917e161b582.
-- Native root/effect columns are kept separate from the historical WotLK spell_dbc schema.

CREATE TABLE IF NOT EXISTS `spell_cata_dbc` (
  `ID` int unsigned NOT NULL,
  `Attributes` int unsigned NOT NULL,
  `AttributesEx` int unsigned NOT NULL,
  `AttributesEx2` int unsigned NOT NULL,
  `AttributesEx3` int unsigned NOT NULL,
  `AttributesEx4` int unsigned NOT NULL,
  `AttributesEx5` int unsigned NOT NULL,
  `AttributesEx6` int unsigned NOT NULL,
  `AttributesEx7` int unsigned NOT NULL,
  `AttributesEx8` int unsigned NOT NULL,
  `AttributesEx9` int unsigned NOT NULL,
  `AttributesEx10` int unsigned NOT NULL,
  `CastingTimeIndex` int unsigned NOT NULL,
  `DurationIndex` int unsigned NOT NULL,
  `PowerType` int unsigned NOT NULL,
  `RangeIndex` int unsigned NOT NULL,
  `Speed` float NOT NULL,
  `SpellVisualID1` int unsigned NOT NULL,
  `SpellVisualID2` int unsigned NOT NULL,
  `SpellIconID` int unsigned NOT NULL,
  `ActiveIconID` int unsigned NOT NULL,
  `Name` text NOT NULL,
  `NameSubtext` text NOT NULL,
  `Description` text NOT NULL,
  `AuraDescription` text NOT NULL,
  `SchoolMask` int unsigned NOT NULL,
  `RuneCostID` int unsigned NOT NULL,
  `SpellMissileID` int unsigned NOT NULL,
  `DescriptionVariablesID` int unsigned NOT NULL,
  `Difficulty` int unsigned NOT NULL,
  `BonusCoefficient` float NOT NULL,
  `ScalingID` int unsigned NOT NULL,
  `AuraOptionsID` int unsigned NOT NULL,
  `AuraRestrictionsID` int unsigned NOT NULL,
  `CastingRequirementsID` int unsigned NOT NULL,
  `CategoriesID` int unsigned NOT NULL,
  `ClassOptionsID` int unsigned NOT NULL,
  `CooldownsID` int unsigned NOT NULL,
  `Unknown38` int unsigned NOT NULL,
  `EquippedItemsID` int unsigned NOT NULL,
  `InterruptsID` int unsigned NOT NULL,
  `LevelsID` int unsigned NOT NULL,
  `PowerDisplayID` int unsigned NOT NULL,
  `ReagentsID` int unsigned NOT NULL,
  `ShapeshiftID` int unsigned NOT NULL,
  `TargetRestrictionsID` int unsigned NOT NULL,
  `TotemsID` int unsigned NOT NULL,
  `RequiredProjectID` int unsigned NOT NULL,
  PRIMARY KEY (`ID`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DELETE FROM `spell_cata_dbc` WHERE `ID` = 5302;
INSERT INTO `spell_cata_dbc` (
  `ID`, `Attributes`, `AttributesEx`, `AttributesEx2`,
  `AttributesEx3`, `AttributesEx4`, `AttributesEx5`, `AttributesEx6`,
  `AttributesEx7`, `AttributesEx8`, `AttributesEx9`, `AttributesEx10`,
  `CastingTimeIndex`, `DurationIndex`, `PowerType`, `RangeIndex`,
  `Speed`, `SpellVisualID1`, `SpellVisualID2`, `SpellIconID`,
  `ActiveIconID`, `Name`, `NameSubtext`, `Description`,
  `AuraDescription`, `SchoolMask`, `RuneCostID`, `SpellMissileID`,
  `DescriptionVariablesID`, `Difficulty`, `BonusCoefficient`, `ScalingID`,
  `AuraOptionsID`, `AuraRestrictionsID`, `CastingRequirementsID`, `CategoriesID`,
  `ClassOptionsID`, `CooldownsID`, `Unknown38`, `EquippedItemsID`,
  `InterruptsID`, `LevelsID`, `PowerDisplayID`, `ReagentsID`,
  `ShapeshiftID`, `TargetRestrictionsID`, `TotemsID`, `RequiredProjectID`
) VALUES (
  5302, 536871312, 1024, 0, 0, 0, 0, 0,
  0, 0, 0, 0, 1, 28, 0, 1,
  0, 0, 0, 0, 0, '', '', '',
  '', 0, 0, 0, 0, 0, 0, 0,
  0, 0, 0, 0, 0, 0, 0, 0,
  0, 0, 0, 0, 0, 0, 0, 0
);

CREATE TABLE IF NOT EXISTS `spelleffect_dbc` (
  `ID` int unsigned NOT NULL,
  `Effect` int unsigned NOT NULL,
  `EffectAmplitude` float NOT NULL,
  `EffectAura` int unsigned NOT NULL,
  `EffectAuraPeriod` int unsigned NOT NULL,
  `EffectBasePoints` int NOT NULL,
  `EffectBonusCoefficient` float NOT NULL,
  `EffectChainAmplitude` float NOT NULL,
  `EffectChainTargets` int unsigned NOT NULL,
  `EffectDieSides` int NOT NULL,
  `EffectItemType` int unsigned NOT NULL,
  `EffectMechanic` int unsigned NOT NULL,
  `EffectMiscValue` int NOT NULL,
  `EffectMiscValueB` int NOT NULL,
  `EffectPointsPerResource` float NOT NULL,
  `EffectRadiusIndex` int unsigned NOT NULL,
  `EffectRadiusMaxIndex` int unsigned NOT NULL,
  `EffectRealPointsPerLevel` float NOT NULL,
  `EffectSpellClassMask1` int unsigned NOT NULL,
  `EffectSpellClassMask2` int unsigned NOT NULL,
  `EffectSpellClassMask3` int unsigned NOT NULL,
  `EffectTriggerSpell` int unsigned NOT NULL,
  `EffectImplicitTargetA` int unsigned NOT NULL,
  `EffectImplicitTargetB` int unsigned NOT NULL,
  `SpellID` int unsigned NOT NULL,
  `EffectIndex` int unsigned NOT NULL,
  `EffectAttributes` int unsigned NOT NULL,
  PRIMARY KEY (`ID`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DELETE FROM `spelleffect_dbc` WHERE `ID` = 153050;
INSERT INTO `spelleffect_dbc` (
  `ID`, `Effect`, `EffectAmplitude`, `EffectAura`,
  `EffectAuraPeriod`, `EffectBasePoints`, `EffectBonusCoefficient`, `EffectChainAmplitude`,
  `EffectChainTargets`, `EffectDieSides`, `EffectItemType`, `EffectMechanic`,
  `EffectMiscValue`, `EffectMiscValueB`, `EffectPointsPerResource`, `EffectRadiusIndex`,
  `EffectRadiusMaxIndex`, `EffectRealPointsPerLevel`, `EffectSpellClassMask1`, `EffectSpellClassMask2`,
  `EffectSpellClassMask3`, `EffectTriggerSpell`, `EffectImplicitTargetA`, `EffectImplicitTargetB`,
  `SpellID`, `EffectIndex`, `EffectAttributes`
) VALUES (
  153050, 6, 0, 4, 0, 0, 0, 1,
  0, 0, 0, 0, 0, 0, 0, 0,
  0, 0, 0, 0, 0, 0, 1, 0,
  5302, 0, 0
);
