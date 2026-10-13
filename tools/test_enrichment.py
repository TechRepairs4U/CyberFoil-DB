import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('enrich', Path(__file__).with_name('enrich_database.py'))
enrich = importlib.util.module_from_spec(spec)
spec.loader.exec_module(enrich)


class MatchingTests(unittest.TestCase):
    def test_safe_variants(self):
        for a,b in [
            ("Cake Mania - Baker's Challenge", 'Cake Mania Bakers Challenge'),
            ('Super Mario Odyssey™', 'Super Mario Odyssey'),
            ('Legend of Zelda, The (USA)', 'The Legend of Zelda'),
            ('Teen Titans 2 (En,Fr)', 'Teen Titans 2'),
            ('Crusader of Centy USA', 'Crusader of Centy'),
            ('Chessmaster (GB Compatible)', 'Chessmaster'),
        ]:
            self.assertEqual(enrich.key(a), enrich.key(b))

    def test_distinct_games_and_variants(self):
        for a,b in [
            ('Super Mario World', 'Super Mario'), ('Teen Titans 2', 'Teen Titans'),
            ('SMB1 Hard Type', 'Super Mario Bros.'), ('Art Alive (Beta)', 'Art Alive'),
            ('Doom 64', 'Doom'), ('Sonic +', 'Sonic'), ('Game #1', 'Game 1'),
            ('Pokemon XD', 'Pokemon Colosseum'), ('Carnage 2 (SMB2 Hack)', 'Carnage 2'),
        ]:
            self.assertNotEqual(enrich.key(a), enrich.key(b))

    def test_facts_require_evidence(self):
        self.assertEqual(enrich.factual_description({'name':'Unknown','platform':'psp','game':{}}), '')
        description = enrich.factual_description({'name':'Test','platform':'psp','game':{
            'genres':[{'name':'Puzzle'}], 'release_dates':[{'platform':38,'y':2006},{'platform':130,'y':2026}],
        }})
        self.assertIn('2006', description)
        self.assertNotIn('2026', description)


if __name__ == '__main__': unittest.main()
