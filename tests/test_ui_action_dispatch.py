import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from lucidshape_mcp import ui_action_dispatch as actions


def candela_grid(path):
    photometry = path.read_text(encoding="ascii").split("TILT=NONE", 1)[1]
    data = [float(val) for val in photometry.replace(",", " ").split()]
    nv, nu = int(data[3]), int(data[4])
    cells = data[13 + nv + nu:]
    return [[x * data[2] for x in cells[i*nv:(i+1)*nv]] for i in range(nu)]


class PhotometryActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "a source with spaces.ies"
        samples = " ".join(str((i+1)**2 % 39) for i in range(35))
        self.source.write_text(
            "IESNA:LM-63-2002\n[TEST] asymmetric UV action test\nTILT=NONE\n"
            "1 -1 1 5 7 3 2 0 0 0\n1 1 0\n-2 -1 0 1 2\n-3 -2 -1 0 1 2 3\n"
            + samples + "\n", encoding="ascii")

    def parameters(self, command="CM_UV_FILTER", **kwargs):
        return actions.validate({"data_path": str(self.source), "command": command, **kwargs})

    def test_accepts_only_known_operations_with_applicable_numeric_parameters(self):
        for command, args in [
            ("rawCommand", {}), ("CM_UV_FILTER", {"passes": True}),
            ("CM_UV_FILTER", {"passes": 0}), ("CM_UV_FILTER", {"passes": 21}),
            ("CM_UV_FILTER", {"factor": 1}), ("CM_UV_FILTER", {"output_path": "source.ies"}),
            ("CM_UV_MIRROR_IN_U", {"passes": 2}), ("CM_UV_SCALE", {}),
            ("CM_UV_SCALE", {"factor": "1 return;"}), ("CM_UV_SCALE", {"factor": float("nan")}),
        ]:
            with self.subTest(command=command, args=args), self.assertRaises(ValueError):
                self.parameters(command, **args)

    def test_staged_script_uses_only_private_names_and_native_operation(self):
        directory = self.root / "job"
        directory.mkdir()
        inputs, expected = actions.stage(directory, self.parameters(passes=2))
        script = (directory / "job.do").read_text(encoding="ascii")
        self.assertNotIn(str(self.source), script)
        self.assertNotIn("@@", script)
        self.assertIn("pass<2", script)
        self.assertIn("lid.filter()", script)
        self.assertEqual(self.source.read_bytes(), (directory / "source.ies").read_bytes())
        self.assertEqual(len(inputs), 1)
        self.assertIn("photometry.ies", expected)

    @unittest.skipUnless(os.environ.get("LUCIDSHAPE_TEST_UI_ENGINE"), "requires configured native LS2.0 engine")
    def test_native_operations_reload_every_output_cell(self):
        original = self.source.read_bytes()
        exe = Path(os.environ["LUCIDSHAPE_TEST_UI_ENGINE"])
        env = {**os.environ, "PATH": str(exe.parent) + os.pathsep + os.environ["PATH"]}
        grid = candela_grid(self.source)
        # Make a signed test LID, since a physical IES does not contain negative intensities.
        (self.root / "fixture.do").write_text(
            'object lid=IESData("source","a source with spaces.ies");\n'
            'lid.setValue(0,0,-7);object st=Store("signed.lid");st << lid.cloneData(0);\n'
            'object null;st=null;cout << "SIGNED_FIXTURE_OK" << endl;\n', encoding="ascii")
        fixture = subprocess.run([str(exe), "-nopp", "fixture.do"], cwd=self.root, env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        self.assertIn(b"SIGNED_FIXTURE_OK", fixture.stdout)
        for command in actions.NATIVE_METHODS:
            with self.subTest(command=command):
                directory = self.root / command
                directory.mkdir()
                args = {"factor": 0.85} if command == "CM_UV_SCALE" else {}
                p = self.parameters(command, **args)
                if command in {"CM_ABSOLUTE", "CM_UV_NEGATIVE_TO_ZERO"}:
                    p = actions.validate({"data_path": str(self.root / "signed.lid"), "command": command})
                actions.stage(directory, p)
                run = subprocess.run([str(exe), "-nopp", str(directory / "job.do")], cwd=directory,
                                     env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
                self.assertIn(b"LUCIDSHAPE_MCP_OK", run.stdout, run.stdout.decode(errors="replace"))
                self.assertEqual(run.returncode, 0)
                result, _ = actions.verify(directory, p)
                self.assertEqual(result["all_cells_compared"], 35)
                out = candela_grid(directory / "photometry.ies")
                if command in {"CM_UV_FILTER", "CM_UV_MAP_FILTER"}:
                    self.assertNotEqual(grid, out)
                if command == "CM_UV_FILTER":
                    self.assertLess(result["after"]["peak_cd"], result["before"]["peak_cd"])
                expected = {
                    "CM_UV_MIRROR_IN_U": lambda i,j: grid[-1-i][j],
                    "CM_UV_MIRROR_IN_V": lambda i,j: grid[i][-1-j],
                    "CM_UV_SWAP_UV": lambda i,j: grid[j][i],
                    "CM_UV_SYMMETRY_U": lambda i,j: (grid[i][j] + grid[-1-i][j]) / 2,
                    "CM_UV_SYMMETRY_V": lambda i,j: (grid[i][j] + grid[i][-1-j]) / 2,
                    "CM_UV_SYMMETRY_UV": lambda i,j: (grid[i][j] + grid[-1-i][j] +
                                                      grid[i][-1-j] + grid[-1-i][-1-j]) / 4,
                    "CM_UV_SCALE": lambda i,j: grid[i][j] * 0.85,
                    "CM_ABSOLUTE": lambda i,j: 7 if (i,j) == (0,0) else grid[i][j],
                    "CM_UV_NEGATIVE_TO_ZERO": lambda i,j: 0 if (i,j) == (0,0) else grid[i][j],
                }.get(command)
                if expected:
                    for i, column in enumerate(out):
                        for j, value in enumerate(column):
                            self.assertAlmostEqual(value, expected(i,j), places=4, msg=f"cell {i},{j}")
                self.assertEqual(self.source.read_bytes(), original)
