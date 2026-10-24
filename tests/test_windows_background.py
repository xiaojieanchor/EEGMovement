import unittest
from scripts.windows_background import validate_pipe_name


class BackgroundTests(unittest.TestCase):
    def test_only_generated_local_pipe_names_are_accepted(self):
        name='EEGMovement-'+'a'*32
        self.assertEqual(validate_pipe_name(name),name)
        for name in ['remote\\pipe','../secret','EEGMovement-other','EEGMovement-'+'a'*31]:
            with self.assertRaises(ValueError):validate_pipe_name(name)

if __name__=='__main__':unittest.main()
