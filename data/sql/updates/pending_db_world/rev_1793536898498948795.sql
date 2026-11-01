-- #205: restore the intended Gilneas voice lines. TDB's creature_text rows carry
-- Lorna's mastiff sound (19696) on Crowley, Liam, and Genn lines; the per-line
-- broadcast sound is authoritative.
UPDATE `creature_text` SET `Sound`=19506 WHERE `CreatureID`=35230 AND `GroupID`=0 AND `ID`=0 AND `BroadcastTextId`=35498;
UPDATE `creature_text` SET `Sound`=19505 WHERE `CreatureID`=35230 AND `GroupID`=1 AND `ID`=0 AND `BroadcastTextId`=35497;
UPDATE `creature_text` SET `Sound`=19622 WHERE `CreatureID`=35551 AND `GroupID`=0 AND `ID`=0 AND `BroadcastTextId`=36106;
UPDATE `creature_text` SET `Sound`=19507 WHERE `CreatureID`=35552 AND `GroupID`=0 AND `ID`=0 AND `BroadcastTextId`=36103;
UPDATE `creature_text` SET `Sound`=19508 WHERE `CreatureID`=35552 AND `GroupID`=1 AND `ID`=0 AND `BroadcastTextId`=36104;
UPDATE `creature_text` SET `Sound`=19509 WHERE `CreatureID`=35552 AND `GroupID`=2 AND `ID`=0 AND `BroadcastTextId`=36105;
UPDATE `creature_text` SET `Sound`=19719 WHERE `CreatureID`=35911 AND `GroupID`=0 AND `ID`=0 AND `BroadcastTextId`=36107;
UPDATE `creature_text` SET `Sound`=19720 WHERE `CreatureID`=35911 AND `GroupID`=1 AND `ID`=0 AND `BroadcastTextId`=36108;
