import sys
import os
sys.path.append(os.getcwd())
import unittest
import itertools

import utils

def dummyf():
    pass

class TestUtils(unittest.TestCase):
    def testTS6UIDGenerator(self):
        uidgen = utils.TS6UIDGenerator('9PY')
        self.assertEqual(uidgen.next_uid(), '9PYAAAAAA')
        self.assertEqual(uidgen.next_uid(), '9PYAAAAAB')

    def test_add_cmd(self):
        # Without name specified, add_cmd adds a command with the same name
        # as the function
        utils.add_cmd(dummyf)
        utils.add_cmd(dummyf, 'TEST')
        # All command names should be automatically lowercased.
        self.assertIn('dummyf', utils.bot_commands)
        self.assertIn('test', utils.bot_commands)
        self.assertNotIn('TEST', utils.bot_commands)

    def test_add_hook(self):
        utils.add_hook(dummyf, 'join')
        self.assertIn('JOIN', utils.command_hooks)
        # Command names stored in uppercase.
        self.assertNotIn('join', utils.command_hooks)
        self.assertIn(dummyf, utils.command_hooks['JOIN'])

    def testIsNick(self):
        self.assertFalse(utils.isNick('abcdefgh', nicklen=3))
        self.assertTrue(utils.isNick('aBcdefgh', nicklen=30))
        self.assertTrue(utils.isNick('abcdefgh1'))
        self.assertTrue(utils.isNick('ABC-def'))
        self.assertFalse(utils.isNick('-_-'))
        self.assertFalse(utils.isNick(''))
        self.assertFalse(utils.isNick(' i lost the game'))
        self.assertFalse(utils.isNick(':aw4t*9e4t84a3t90$&*6'))
        self.assertFalse(utils.isNick('9PYAAAAAB'))
        self.assertTrue(utils.isNick('_9PYAAAAAB\\'))

    def testIsChannel(self):
        self.assertFalse(utils.isChannel(''))
        self.assertFalse(utils.isChannel('lol'))
        self.assertTrue(utils.isChannel('#channel'))
        self.assertTrue(utils.isChannel('##ABCD'))

    def testIsServerName(self):
        self.assertFalse(utils.isServerName('Invalid'))
        self.assertTrue(utils.isServerName('services.'))
        self.assertFalse(utils.isServerName('.s.s.s'))
        self.assertTrue(utils.isServerName('Hello.world'))
        self.assertFalse(utils.isServerName(''))
        self.assertTrue(utils.isServerName('pylink.overdrive.pw'))
        self.assertFalse(utils.isServerName(' i lost th.e game'))

    def testJoinModes(self):
        res = utils.joinModes({('l', '50'), ('n', None), ('t', None)})
        # Sets are orderless, so the end mode could be scrambled in a number of ways.
        # Basically, we're looking for a string that looks like '+ntl 50' or '+lnt 50'.
        possible = ['+%s 50' % ''.join(x) for x in itertools.permutations('lnt', 3)]
        self.assertIn(res, possible)
        # Without any arguments, make sure there is no trailing space.
        self.assertEqual(utils.joinModes({('t', None)}), '+t')
        self.assertEqual(utils.joinModes(set()), '+')

if __name__ == '__main__':
    unittest.main()