DELETE FROM `build_info` WHERE `build` = 15595;
INSERT INTO `build_info`
    (`build`, `majorVersion`, `minorVersion`, `bugfixVersion`, `hotfixVersion`, `winAuthSeed`, `win64AuthSeed`, `mac64AuthSeed`, `winChecksumSeed`, `macChecksumSeed`)
VALUES
    (15595, 4, 3, 4, NULL, NULL, NULL, NULL, NULL, NULL);

ALTER TABLE `realmlist` ALTER COLUMN `gamebuild` SET DEFAULT 15595;
