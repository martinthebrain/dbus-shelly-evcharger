# SPDX-License-Identifier: GPL-3.0-or-later
from tests.wallbox_state_controller_support import *


class TestServiceStateControllerQuinary(ServiceStateControllerTestBase):
    def test_load_runtime_state_handles_missing_path_invalid_json_and_load_config_errors(self) -> None:
        service = make_runtime_state_service(
            virtual_mode=0,
            virtual_enable=0,
            virtual_startstop=1,
        )
        controller = ServiceStateController(service, self._normalize_mode)
        controller.load_runtime_state()

        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "state.json")
            service.runtime_state_path = path
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{bad json")
            with patch(STATE_RUNTIME_LOG_WARNING) as warning_mock:
                controller.load_runtime_state()
            warning_mock.assert_called_once()

        parser = tempfile.TemporaryDirectory()
        self.addCleanup(parser.cleanup)
        missing_config_controller = ServiceStateController(service, self._normalize_mode)
        with patch.object(ServiceStateController, "config_path", return_value=os.path.join(parser.name, "missing.ini")):
            with self.assertRaises(ValueError):
                missing_config_controller.load_config()
