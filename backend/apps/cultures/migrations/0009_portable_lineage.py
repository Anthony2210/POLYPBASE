import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cultures", "0008_transfer_v2_source_package"),
        ("organizations", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="transferitem",
            name="lineage_snapshot",
            field=models.JSONField(blank=True, default=None, editable=False, null=True),
        ),
        migrations.CreateModel(
            name="PortableLineageNode",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("node_id", models.UUIDField(default=uuid.uuid4, editable=False)),
                ("local_box", models.OneToOneField(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="portable_lineage_node", to="cultures.box")),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="portable_lineage_nodes", to="organizations.organization")),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("organization", "node_id"), name="portable_node_identity_unique"),
                ],
            },
        ),
        migrations.CreateModel(
            name="PortableLineageEdge",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("edge_id", models.UUIDField(default=uuid.uuid4, editable=False)),
                ("relationship_type", models.CharField(choices=[("subculture", "Repiquage"), ("sexual_reproduction", "Reproduction sexuée"), ("historical_import", "Import historique"), ("other", "Autre"), ("transfer", "Transfert")], default="subculture", max_length=40)),
                ("transfer_id", models.UUIDField(blank=True, null=True)),
                ("item_id", models.UUIDField(blank=True, null=True)),
                ("local_lineage", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="portable_lineage_edge", to="cultures.boxlineage")),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="portable_lineage_edges", to="organizations.organization")),
                ("source_node", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="outgoing_edges", to="cultures.portablelineagenode")),
                ("target_node", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="incoming_edges", to="cultures.portablelineagenode")),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("organization", "edge_id"), name="portable_edge_identity_unique"),
                    models.CheckConstraint(
                        condition=~models.Q(source_node=models.F("target_node")),
                        name="portable_edge_not_self",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(relationship_type__in=[
                            "subculture", "sexual_reproduction", "historical_import", "other", "transfer",
                        ]),
                        name="portable_edge_type_valid",
                    ),
                    models.CheckConstraint(
                        condition=(
                            models.Q(relationship_type="transfer", transfer_id__isnull=False, item_id__isnull=False)
                            | (
                                ~models.Q(relationship_type="transfer")
                                & models.Q(transfer_id__isnull=True, item_id__isnull=True)
                            )
                        ),
                        name="portable_edge_provenance_valid",
                    ),
                    models.CheckConstraint(
                        condition=~models.Q(relationship_type="transfer") | models.Q(local_lineage__isnull=True),
                        name="portable_transfer_no_local_lineage",
                    ),
                ],
            },
        ),
    ]
