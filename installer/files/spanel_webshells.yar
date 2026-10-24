/*
 * SPanel Enterprise Real-Time YARA Webshell & Crypto-Miner Ruleset
 * Installed at /etc/spanel/yara/spanel_webshells.yar
 */

rule SPanel_PHP_Obfuscated_Eval_Base64 {
    meta:
        description = "Detects obfuscated PHP eval/assert with base64/gzinflate/str_rot13"
        severity = "critical"
    strings:
        $php = "<?php" nocase
        $s1 = "eval(base64_decode(" nocase
        $s2 = "eval(gzinflate(" nocase
        $s3 = "eval(gzuncompress(" nocase
        $s4 = "assert(base64_decode(" nocase
    condition:
        $php and any of ($s*)
}

rule SPanel_PHP_Known_Webshell_Families {
    meta:
        description = "Detects classic PHP webshell families (WSO, b374k, r57, c99, Alfa, FilesMan)"
        severity = "critical"
    strings:
        $w1 = "FilesMan" nocase
        $w2 = "b374k" nocase
        $w3 = "r57shell" nocase
        $w4 = "c99shell" nocase
        $w5 = "Alfa-Shell" nocase
        $w6 = "IndoXploit" nocase
        $w7 = "p0wny@shell" nocase
    condition:
        any of ($w*)
}

rule SPanel_CryptoMiner_XMRig {
    meta:
        description = "Detects XMRig / Stratum cryptocurrency miner strings with pool/config context"
        severity = "high"
    strings:
        $pool1 = "stratum+tcp://" nocase
        $pool2 = "stratum+ssl://" nocase
        $bin = "xmrig" nocase
        $algo = "cryptonight" nocase
        $cfg1 = "--donate-level" nocase
        $cfg2 = "\"rx/0\"" nocase
    condition:
        ($pool1 or $pool2) and ($bin or $algo or $cfg1 or $cfg2) or ($bin and ($algo or $cfg1 or $cfg2))
}
