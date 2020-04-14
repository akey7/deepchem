"""
Generates protein-ligand docked poses using Autodock Vina.
"""
from deepchem.utils import mol_xyz_util
import logging
import numpy as np
import os
import tempfile
from subprocess import call
from deepchem.utils.rdkit_util import add_hydrogens_to_mol
from deepchem.dock.binding_pocket import RFConvexHullPocketFinder
from deepchem.utils import rdkit_util

logger = logging.getLogger(__name__)

DATA_DIR = deepchem.utils.get_data_dir()


class PoseGenerator(object):
  """Abstract superclass for all pose-generation routines."""

  def generate_poses(self, protein_file, ligand_file, out_dir=None):
    """Generates the docked complex and outputs files for docked complex."""
    raise NotImplementedError


def write_conf(receptor_filename,
               ligand_filename,
               centroid,
               box_dims,
               conf_filename,
               exhaustiveness=None):
  """Writes Vina configuration file to disk."""
  with open(conf_filename, "w") as f:
    f.write("receptor = %s\n" % receptor_filename)
    f.write("ligand = %s\n\n" % ligand_filename)

    f.write("center_x = %f\n" % centroid[0])
    f.write("center_y = %f\n" % centroid[1])
    f.write("center_z = %f\n\n" % centroid[2])

    f.write("size_x = %f\n" % box_dims[0])
    f.write("size_y = %f\n" % box_dims[1])
    f.write("size_z = %f\n\n" % box_dims[2])

    if exhaustiveness is not None:
      f.write("exhaustiveness = %d\n" % exhaustiveness)


class VinaPoseGenerator(PoseGenerator):
  """Uses Autodock Vina to generate binding poses.

  This class uses Autodock Vina to make make predictions of binding poses. It
  downloads the Autodock Vina executable for your system to your specified
  DEEPCHEM_DATA_DIR (remember this is an environment variable you set) and
  invokes the executable to perform pose generation for you.
  """

  def __init__(self, exhaustiveness=10, detect_pockets=True, sixty_four_bits=True):
    """Initializes Vina Pose Generator

  
    Params
    ------
    exhaustiveness: int, optional
      Tells Autodock Vina how exhaustive it should be with pose generation.
    detect_pockets: bool, optional
      If True, attempt to automatically detect binding pockets for this protein.
    sixty_four_bits: bool, optional
      Specifies whether this is a 64-bit machine. Needed to download the correct executable. 
    """
    self.vina_dir = os.path.join(DATA_DIR, "autodock_vina_1_1_2_linux_x86")
    self.exhaustiveness = exhaustiveness
    self.detect_pockets = detect_pockets
    if self.detect_pockets:
      self.pocket_finder = RFConvexHullPocketFinder()
    if not os.path.exists(self.vina_dir):
      logger.info("Vina not available. Downloading")
      if platform.system() == 'Linux':
        filename = "http://vina.scripps.edu/download/autodock_vina_1_1_2_linux_x86.tgz" 
        dirname = "autodock_vina_1_1_2_linux_x86"
      elif platform.system() == 'Darwin':
        if sixty_four_bits:
          filename = "http://vina.scripps.edu/download/autodock_vina_1_1_2_mac_64bit.tar.gz"
          dirname = "autodock_vina_1_1_2_mac_catalina_64bit"
        else:
          filename = "http://vina.scripps.edu/download/autodock_vina_1_1_2_mac.tgz"
          dirname = "autodock_vina_1_1_2_linux_x86"
      else:
        raise ValueError("This module can only run on Linux or Mac. If you are on Windows, please try using a cloud platform to run this code instead.")
      wget_cmd = "wget -nv -c -T 15 %s" % filename
      call(wget_cmd.split())
      logger.info("Downloaded Vina. Extracting")
      untar_cmd = "tar xzvf %s" % filename
      call(untar_cmd.split())
      logger.info("Moving to final location")
      mv_cmd = "mv %s %s" % (dirname DATA_DIR)
      call(mv_cmd.split())
      logger.info("Cleanup: removing downloaded vina tar.gz")
      rm_cmd = "rm %s" % filename
      call(rm_cmd.split())
    self.vina_cmd = os.path.join(self.vina_dir, "bin/vina")

  def generate_poses(self,
                     protein_file,
                     ligand_file,
                     centroid=None,
                     box_dims=None,
                     dry_run=False,
                     out_dir=None):
    """Generates the docked complex and outputs files for docked complex.

    Params
    ------
    protein_file: str
      The filename for the protein file. If "foo.pdb" is the protein file,
      there must be a second "foo.pdbqt" file in the same directory for this
      function to be invoked.
    ligand_file: str
      The filename for the ligand file
    centroid: tuple, optional
      The centroid to dock against. Is computed is not specified.
    TODO
    """
    if out_dir is None:
      out_dir = tempfile.mkdtemp()

    # Prepare receptor
    receptor_name = os.path.basename(protein_file).split(".")[0]
    protein_hyd = os.path.join(out_dir, "%s_hyd.pdb" % receptor_name)
    protein_pdbqt = os.path.join(out_dir, "%s.pdbqt" % receptor_name)

    # Get protein centroid and range
    # TODO(rbharath): Need to add some way to identify binding pocket, or this is
    # going to be extremely slow!
    if centroid is not None and box_dims is not None:
      protein_centroid = centroid
    else:
      if not self.detect_pockets:
        receptor_mol = rdkit_util.load_molecule(
            protein_file, calc_charges=True, add_hydrogens=True)
        rdkit_util.write_molecule(receptor_mol[1], protein_hyd, is_protein=True)
        rdkit_util.write_molecule(
            receptor_mol[1], protein_pdbqt, is_protein=True)
        protein_centroid = mol_xyz_util.get_molecule_centroid(receptor_mol[0])
        protein_range = mol_xyz_util.get_molecule_range(receptor_mol[0])
        box_dims = protein_range + 5.0
      else:
        logger.info("About to find putative binding pockets")
        pockets, pocket_atoms_maps, pocket_coords = self.pocket_finder.find_pockets(
            protein_file, ligand_file)
        # TODO(rbharath): Handle multiple pockets instead of arbitrarily selecting
        # first pocket.
        logger.info("Computing centroid and size of proposed pocket.")
        pocket_coord = pocket_coords[0]
        protein_centroid = np.mean(pocket_coord, axis=1)
        pocket = pockets[0]
        (x_min, x_max), (y_min, y_max), (z_min, z_max) = pocket
        x_box = (x_max - x_min) / 2.
        y_box = (y_max - y_min) / 2.
        z_box = (z_max - z_min) / 2.
        box_dims = (x_box, y_box, z_box)

    # Prepare receptor
    ligand_name = os.path.basename(ligand_file).split(".")[0]
    ligand_pdbqt = os.path.join(out_dir, "%s.pdbqt" % ligand_name)

    # TODO(rbharath): Generalize this so can support mol2 files as well.
    ligand_mol = rdkit_util.load_molecule(
        ligand_file, calc_charges=True, add_hydrogens=True)
    rdkit_util.write_molecule(ligand_mol[1], ligand_pdbqt)
    # Write Vina conf file
    conf_file = os.path.join(out_dir, "conf.txt")
    write_conf(
        protein_pdbqt,
        ligand_pdbqt,
        protein_centroid,
        box_dims,
        conf_file,
        exhaustiveness=self.exhaustiveness)

    # Define locations of log and output files
    log_file = os.path.join(out_dir, "%s_log.txt" % ligand_name)
    out_pdbqt = os.path.join(out_dir, "%s_docked.pdbqt" % ligand_name)
    # TODO(rbharath): Let user specify the number of poses required.
    if not dry_run:
      logger.info("About to call Vina")
      call(
          "%s --config %s --log %s --out %s" % (self.vina_cmd, conf_file,
                                                log_file, out_pdbqt),
          shell=True)
    # TODO(rbharath): Convert the output pdbqt to a pdb file.

    # Return docked files
    return protein_hyd, out_pdbqt
