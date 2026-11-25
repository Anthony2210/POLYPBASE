"""A recorded merged alias cannot acquire its own ownership identity."""

from django.test import TestCase
from rest_framework.exceptions import ValidationError

from .box_ownership import enroll_origin_box
from .models import Box, BoxOwnershipEpisode, GlobalBoxIdentity
from .pca_test_fixtures import PcaFixtureMixin


class PcaAliasOwnershipTests(PcaFixtureMixin, TestCase):
    def test_merged_alias_cannot_be_enrolled(self):
        self.record_merged_history()
        with self.assertRaises(ValidationError):
            enroll_origin_box(organization=self.org, box=Box.objects.get(pk=self.donor.pk))
        self.assertFalse(BoxOwnershipEpisode.objects.exists())
        self.assertFalse(GlobalBoxIdentity.objects.exists())
