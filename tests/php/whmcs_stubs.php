<?php
/**
 * The few WHMCS functions and classes modules/servers/spanel uses, recorded
 * instead of executed, so the module can be exercised without WHMCS.
 */

namespace WHMCS\Database {
    class Capsule
    {
        public static function table($name)
        {
            return new CapsuleQuery($name);
        }

        public static function raw($value)
        {
            return 'RAW:' . $value;
        }
    }

    class CapsuleQuery
    {
        public $table;
        public $wheres = [];

        public function __construct($table)
        {
            $this->table = $table;
        }

        public function where()
        {
            $this->wheres[] = func_get_args();
            return $this;
        }

        public function first($columns = [])
        {
            $rows = $GLOBALS['CAPSULE_FIRST'][$this->table] ?? null;
            return $rows === null ? null : (object) $rows;
        }

        public function update($values)
        {
            $GLOBALS['CAPSULE_UPDATES'][] = ['table' => $this->table, 'wheres' => $this->wheres, 'values' => $values];
            return 1;
        }
    }
}

namespace {
    define('WHMCS', true);

    $GLOBALS['LOCAL_API_CALLS'] = [];
    $GLOBALS['MODULE_LOG'] = [];
    $GLOBALS['CAPSULE_FIRST'] = [];
    $GLOBALS['CAPSULE_UPDATES'] = [];

    function localAPI($command, $values = [], $adminUser = '')
    {
        $GLOBALS['LOCAL_API_CALLS'][] = ['command' => $command, 'values' => $values];
        return ['result' => 'success'];
    }

    function logModuleCall($module, $action, $request, $response, $data, $replaceVars = [])
    {
        $GLOBALS['MODULE_LOG'][] = compact('module', 'action', 'request', 'response', 'data', 'replaceVars');
    }
}
